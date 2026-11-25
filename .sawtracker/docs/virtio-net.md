# SO-103 A3: `svc-virtio-net`, the runner's network peer, and `net_ping`

Revision 3. Design for unit A3 of the networking design
(`.leadwork/networking.md`, revision 2, §10 l.1385-1390). Design only:
nothing here changes the tree. The facts it rests on were checked against the
code on current main (`850c5849`, fetched over HTTPS from
`github.com/swoodtke/sawos` into `so103a3/main`; the local checkout is at the
same commit), the spec, the virtio 1.2 specification (OASIS cs01, read in
full from a text conversion of the published HTML, `so103a3/spec/v.txt`),
QEMU 11.1.3 as installed and QEMU's source (master: `hw/virtio/virtio-mmio.c`,
`hw/net/virtio-net.c`, `hw/intc/sifive_plic.c`, `net/stream.c`,
`net/stream_data.c`). The experiments are in the scratch directory
(`so103a3/exp1.sh` to `exp4b.py` ran; `exp5.sh` is written and not yet run,
§11). The owner's rulings of Nov 25 are `rulings-nov25.md`; the reviewer's
threads on revision 1 are answered in `a3-replies.md`.

## Changes in revision 3

The owner ruled on the physical address and on contiguous memory
(`rulings-nov25.md`). This revision follows the rulings:

- **The physical address comes from the Memory, not the Mapping.** Unit K1 is
  now `MemoryOp.Physical` (op 2) on `MemoryRight.Physical` (bit 12), which a
  pool carries at the same bit (`PoolRight.Physical`) for what it allocates.
  It answers the object's base and length through a two-word record. The
  numbers were re-checked on main (§3.4).
- **Contiguous.** `PoolOp.Allocate`'s third word gains a flag,
  `AllocateFlag.Contiguous` (bit 8), beside `AllocateFrom`; an unknown bit is
  the `BadArg` fault. `Physical` answers only for an object created with the
  flag or a boot allocation, and the `NoResource` status for any other
  (§3.4).
- **Off unless asked for, at both ends.** A process's declaration asks in its
  `[ceilings]`; the image grants it in a new per-process `ceilings` table,
  which is the image's ceiling and is intersected with the declaration's. The
  defaults change so that a process with no `[ceilings]`, and a record with no
  `rights`, get every right except `physical`. The builder refuses, by name,
  an ask without a grant, a grant without an ask, and an image list that
  strips a right one of the process's own records keeps (§3.5).
- **The `dma = true` opt-in and `MappingOp.Physical` are gone**, and with
  them A3-K1b. The image `ceilings` table and the default changes are priced
  as a general builder unit (B1), filed under the limits issue, which the
  networking track depends on (§3.6). The minimum that keeps "off by default"
  true at every merge rides with the kernel unit.
- **The driver allocates each client's ring.** `netdevproto`'s `Attach` is
  reversed: the request carries slot counts and no handle; the driver
  allocates the ring from its own pool, contiguous, lays it out, and answers
  with a handle the client may map, minted without `Physical`. A new status,
  `NoMemory`, answers a ring the pool cannot hold. The `pktring` section gains
  the constructors this needs (§4.2, §5.2).
- **Q5's argument is simpler (§4.5):** every byte the device can address lies
  in the driver's one pool, which the image binds to the driver alone and the
  driver's record keeps without `Transfer` or `Mint`, so it cannot be shared
  by any process's code; a ring a client still maps cannot return to it.
- **Cases (§7.3):** a pin for each new check: `Physical` refused on a
  non-contiguous object, the opt-in refused without the image's grant, a grant
  without the declaration's ask, an image list stripping a kept right, the
  client's ring handle without `Physical`, a ring too large for the pool. The
  two driver-side `Attach` refusal cases are gone, since the client no longer
  gives the driver memory.
- **Units and order (§8):** K1 (kernel, with the builder's default), B1 (the
  general builder unit), K2, A3-1 to A3-5, and the optional A3-K3.
- **Questions (§9):** Q1 is ruled. Q12 and Q13 are new: `physical` as an
  opt-in on the `pool` kind as well as `memory`, and `NoResource` as the
  status for a non-contiguous object.

Revision 2's changes (the reviewer's twenty threads) stand: reset confirmed
before unmap, the failure-class table, cache coherence, config access widths,
the ISR mask, the `pktring` cursor rules, the peer's scripts, the forced
abandonment case, the virtio citations, the bounded loops, the exit lines in
the transcript, the SHOULD-accept feature policy, the barrier before the
`used.flags` read, doorbell ranges against the ring, and the PLIC's re-fire
rule.

The short version:

- Modern virtio-mmio (version 2). `VERSION_1` and `MAC` required; `STATUS`,
  `MTU` and `ORDER_PLATFORM` taken when offered; a device offering
  `ACCESS_PLATFORM` refused; nothing else. A 12-byte header in a descriptor
  of its own; frames zero-copy in the ring's slots.
- The board lists each virtio-mmio transport as a device of type
  `virtio-mmio`; the image names the one the driver takes; the driver checks
  `DeviceID` and refuses anything but a network device.
- Every byte the device sees is in the driver's own pool: its queues and
  each client's ring, all allocated contiguous. The driver learns their bus
  addresses with `MemoryOp.Physical`, which it holds only because its
  declaration asks for it and its image grants it.
- arm64 also needs a second device window in its HAL.
- The peer is a Saw tool (`tools/netpeer`), one process per case on a
  loopback port of its own; QEMU is started paused and resumed only once QMP
  says the stream is connected.
- Nine units, one of them optional (§8).

---

## 1. Device and transport

### 1.1 Legacy or modern

| Fact | Evidence |
|---|---|
| QEMU's `virtio-mmio` property `force-legacy` defaults to `on` on both `virt` machines | `-device virtio-mmio,help` prints `force-legacy=<bool> - on/off (default: on)`; `virtio-mmio.c` ~l.615 `DEFINE_PROP_BOOL("force-legacy", VirtIOMMIOProxy, legacy, true)`; `info qtree` shows `force-legacy = true` on every transport (exp1, `rv-default.txt`, `a64-default.txt`) |
| By default the transport reads `Version` 1 | `xp /4wx 0x10007000` → `0x74726976 0x00000001 0x00000000 0x554d4551` (exp1) |
| With `-global virtio-mmio.force-legacy=false` it reads `Version` 2 | riscv32 `0x10008000: 0x74726976 0x00000002 0x00000001 0x554d4551`; arm64 `0x0a003e00:` the same (exp1, `rv-modern.txt`, `a64-modern.txt`) |
| On a modern transport QEMU adds `VIRTIO_F_VERSION_1` to what it offers and enforces nothing about it | `virtio-mmio.c` `virtio_mmio_pre_plugged` ~l.595; the `FEATURES_OK` write commits the driver's features with no check ~l.380 |

**Choose modern (version 2).** The case's QEMU line carries
`-global virtio-mmio.force-legacy=false` (§6.3).

- The legacy interface (virtio 1.2 §4.2.4) places a queue by a page frame
  number in one contiguous layout (`GuestPageSize`, `QueueAlign`,
  `QueuePFN`); modern places the descriptor table, the driver area and the
  device area independently by 64-bit address (§4.2.2, Table 4.1:
  `QueueDescLow/High` 0x080/0x084, `QueueDriverLow/High` 0x090/0x094,
  `QueueDeviceLow/High` 0x0a0/0x0a4), which suits a queue placed inside one
  small allocation (§4.1).
- Modern has `FEATURES_OK` (§2.1, §3.1.1 steps 5-6), so a device that cannot
  run with the driver's subset says so before the driver goes live.
- Modern structures are little-endian (§1.4, Structure Specifications: "le16,
  le32, le64 ... little-endian byte order") and the net header is fixed at 12
  bytes (§5.1.6.3); legacy's header length depends on `MRG_RXBUF`.
- The driver refuses a version-1 transport by name (§7.3, `net_legacy`).

### 1.2 Features

Feature bits from virtio 1.2: §5.1.3 for the network device, §6 for the
reserved bits. The driver requirements are §6.1 and §5.1.4.2. QEMU's default
offer was read in exp2.

| Bit | Name | Decision | Why |
|---|---|---|---|
| 32 | `VERSION_1` | **required** | §6.1: "A driver MUST accept VIRTIO_F_VERSION_1 if it is offered" |
| 5 | `MAC` | **required** | the MAC the Attach reply carries (netdevproto `AttachInfo.mac`); §5.1.4.2 "A driver SHOULD negotiate VIRTIO_NET_F_MAC if the device offers it". A device without it is refused rather than given a made-up address (§5.1.4.2 allows a locally administered one); QEMU always offers it |
| 16 | `STATUS` | **taken when offered** | link up and down (§5.3). Without it the driver reports the link up, as §5.1.4.2 says: "If the driver does not negotiate VIRTIO_NET_F_STATUS, it SHOULD assume the link is active" |
| 3 | `MTU` | **taken when offered** | §5.1.4.2: "A driver SHOULD negotiate VIRTIO_NET_F_MTU if the device offers it", and then "MUST supply enough receive buffers to receive at least one receive packet of size mtu". A slot (2048) holds 12 + 14 + an MTU up to 2022. The driver answers `Attach` with min(`mtu`, 1500), 1500 being the stack's (`ether.MTU`); an offered `mtu` below 68 is a device fault (§5.1.4.1: "between 68 and 65535"). Not offered on QEMU's defaults (`host_mtu` 0) |
| 36 | `ORDER_PLATFORM` | **taken when offered** | §6.1 "A driver SHOULD accept VIRTIO_F_ORDER_PLATFORM if it is offered" and then "MUST use the barriers suitable for hardware devices", which `fence iorw, iorw` and `dsb sy` are (§4.3) |
| 33 | `ACCESS_PLATFORM` | **a device offering it is refused** | §6.1: a driver SHOULD accept it, "and it MUST then either disable the IOMMU or configure the IOMMU". There is no IOMMU here to do either with, and an offer says the device's view of addresses may be translated, which this driver cannot honour. Refused by name, with `FAILED` (§4.3). QEMU offers it only with `iommu_platform=on` (default off) |
| 34 | `RING_PACKED` | declined | §6.1 SHOULD accept; this driver implements the split ring only, and the SHOULD is the deviation it takes. QEMU's `packed` defaults off |
| 39 | `NOTIF_CONFIG_DATA` | declined | §6.1 SHOULD accept; its data is defined for PCI's notification structure ("For more details about driver notifications over PCI see 4.1.5.2"), which an MMIO transport does not have |
| 62 | `STANDBY` | declined | §5.1.4.2 SHOULD negotiate; it makes the device a failover standby for a primary with the same MAC, and these images have no primary |
| 0, 1 | `CSUM`, `GUEST_CSUM` | declined | the stack computes and checks every checksum (`drivers/netstack`); flags stay 0 (§5.1.6.2.1) |
| 2, 6-14, 41-42, 55-61, 63 | `CTRL_GUEST_OFFLOADS`, `GSO`, GUEST and HOST TSO/UFO/ECN, `HOST_USO` and `GUEST_USO4/6`, RSC, `HASH_REPORT`, `GUEST_HDRLEN` (59), `RSS`, `RSC_EXT`, `SPEED_DUPLEX` (63) | declined | one frame per buffer, no offload, no hash; nothing reads speed |
| 15 | `MRG_RXBUF` | declined | one 2048-byte slot holds a whole frame; without it the device "MUST set num_buffers to 1" (§5.1.6.4.1) and uses one descriptor chain per frame (§5.1.6.3.2) |
| 17-23 | `CTRL_VQ` (and `CTRL_RX`, `CTRL_VLAN`, `GUEST_ANNOUNCE`, `MQ`, `CTRL_MAC_ADDR`) | declined | no control queue, so one queue pair (§5.1.2: "N=1 if neither VIRTIO_NET_F_MQ nor VIRTIO_NET_F_RSS are negotiated"); without `CTRL_RX` QEMU delivers every frame, and the stack's own filter drops what is not ours (`netstack` lib.saw l.37-41) |
| 28, 29 | `INDIRECT_DESC`, `EVENT_IDX` | declined | two-descriptor chains are enough; notification by `used.flags` (§4.3) |
| 35, 37, 38, 40 | `IN_ORDER`, `SR_IOV`, `NOTIFICATION_DATA`, `RING_RESET` | declined | the driver handles completions out of order (§4.4); SR-IOV is PCI-only (§6.1); no queue is reset alone |

A feature the driver does not take is never written back to
`DriverFeatures`. The negotiated word on QEMU's defaults is
`VERSION_1 | STATUS | MAC`, `0x1_0001_0020`. Exp2 read the config space with
that offer: the MAC `52:54:00:12:34:56` at 0x100 and `status` 0x0001
(`VIRTIO_NET_S_LINK_UP`, §5.1.4) at 0x106, nothing past it (`0xffffffff`), so
neither `MQ` nor `MTU` was offered. `QueueNumMax` reads 256 on both queues
(exp2, `xp 0x10008034`).

### 1.3 The header

With `VERSION_1` and no `MRG_RXBUF`, no hash and no tunnel, the header is the
12-byte `struct virtio_net_hdr` with `num_buffers` (§5.1.6: `flags` u8,
`gso_type` u8, `hdr_len` le16, `gso_size` le16, `csum_start` le16,
`csum_offset` le16, `num_buffers` le16; §5.1.6.3 "The 12-byte struct
virtio_net_hdr"). QEMU picks the same length on that path
(`virtio-net.c` `virtio_net_set_mrg_rx_bufs` ~l.1390-1400: with `version_1`
the fallback is `sizeof(struct virtio_net_hdr_mrg_rxbuf)`).

- **Transmit:** all twelve bytes zero: `flags` 0, `gso_type`
  `VIRTIO_NET_HDR_GSO_NONE` (0), `num_buffers` 0 (§5.1.6.2, §5.1.6.2.1). The
  device only reads it, so one zeroed header serves every TX chain.
- **Receive:** the device writes it into a header buffer of the driver's own
  per RX chain (§4.1). The driver ignores `flags` it does not know
  (§5.1.6.4.2) and checks `num_buffers == 1` (§4.3).
- The header and the frame are two descriptors of one chain. §2.7.4 (Message
  Framing) uses exactly this example: a network transmit buffer "could be
  most simply placed in the descriptor table as a 12 byte output descriptor
  followed by a 1514 byte output descriptor". QEMU reads the header from the
  first element and refuses only a chain with no element at all
  (`virtio_net_flush_tx` ~l.2350, "virtio-net header not in first element"
  when `out_num < 1`).

---

## 2. How the driver gets its device

### 2.1 What the machines have (verified)

| | riscv32 `virt` (and the flat profile) | arm64 `virt` |
|---|---|---|
| Transports | 8, `virtio_mmio@10001000` to `@10008000`, stride 0x1000 (`rv.dts` l.127-181) | 32, `virtio_mmio@a000000` to `@a003e00`, stride 0x200 (`a64.dts` l.39-261) |
| Register region | 0x200 (`info qtree`: `mmio 0000000010008000/0000000000000200`; `virtio-mmio.c` ~l.650) | 0x200 |
| Interrupt of transport `i` (QEMU bus `virtio-mmio-bus.i`) | PLIC source `i + 1` (`interrupts = <0x08>` at 0x1000_8000) | SPI `16 + i`, GIC INTID `48 + i` (`interrupts = <0x00 0x2f 0x01>` at 0x0A00_3E00: SPI 47, INTID 79) |
| Where a `-device` with no `bus=` lands | the highest transport: bus 7, 0x1000_8000 (`info qtree`) | bus 31, 0x0A00_3E00 |
| `bus=virtio-mmio-bus.N` | places it at transport N: bus 0 reads `DeviceID` 1 at 0x1000_1000 (exp1, `rv-bus0.txt`) | the same mechanism |
| An empty transport | `DeviceID` 0 (`xp 0x10007000`, exp1) | the same |
| `dma-coherent` on the transport nodes | none (the two in `rv.dts` are on fw-cfg and the PCI host) | every `virtio_mmio` node (`a64.dts` l.40 and the 31 like it) |

The board files already number lines as the interrupt controller does: the
arm64 console is `line = 33`, its SPI 1 (`hal/arm64/board.toml`).

QEMU drives a transport's line as a level, `level = (isr != 0)`
(`virtio-mmio.c` `virtio_mmio_update_irq` ~l.455-465), recomputed at every
notification and when the driver writes `InterruptACK` (~l.345).

- **arm64.** The device tree marks the SPIs edge-triggered (`a64.dts` l.41),
  but the HAL never writes `GICD_ICFGR` (`hal/arm64/kernel/lib.saw`
  l.2067-2079, `reset`), so the lines stay level-sensitive, as the PL011's
  is. Spec §9's mask-on-fire, ack-to-rearm (spec.md l.4398-4402) then holds
  as written: a line still high at `irq.ack()` fires again.
- **riscv32.** QEMU's PLIC does not re-fire a line by its staying high: a
  source sets its pending bit only when it calls in with a level above zero
  (`sifive_plic.c` `sifive_plic_irq_request`: `if (level > 0)
  sifive_plic_set_pending(s, irq, true)`), a claim clears it, and a
  completion does not look at the input again. Nothing is lost by that:
  every notification QEMU raises calls in again (`virtio_notify` →
  `virtio_mmio_update_irq` → `qemu_set_irq`), and the driver drains the used
  rings after writing `InterruptACK` (§4.4 step 4), so a completion that came
  before the ack is drained and one that comes after it sets pending anew.

### 2.2 The protection grain decides the arm64 rows

- riscv32's grain is 4 bytes and a device window is a naturally aligned power
  of two (`hal/riscv32-common/kernel/lib.saw` l.269-278): a 0x1000 window at
  each transport is legal, one transport per window.
- arm64's grain is a page and a device window is whole pages
  (`hal/arm64/kernel/lib.saw` l.600-608); `sosimage board` refuses a device
  row off the grain (`tools/sosimage/src/boardcheck.saw` l.298-299). A 0x200
  transport cannot be a window of its own: the smallest window holds eight
  transports.

So one device instance, in the one-process-per-device sense, is one
transport on riscv32 and one page of eight transports on arm64. The rows
follow that, and the runner places a device only at the first transport of a
page on arm64, so the transport is at the window's start on both boards and
the driver needs no offset.

### 2.3 The board rows

`hal/riscv32/board.toml` and `hal/riscv32-flat/board.toml` (the same machine)
gain eight rows, named by QEMU's bus index:

```toml
# The virtio-mmio transports, one per row: QEMU's `virtio-mmio-bus.N` is at
# 0x1000_1000 + N * 0x1000 on PLIC source N + 1. What sits behind one is the
# emulator's command line; an empty transport reads DeviceID 0.
[devices.VIRTIO0]
type = "virtio-mmio"
base = 0x1000_1000
len = 0x1000
line = 1
# ... VIRTIO1 to VIRTIO7, up to base = 0x1000_8000, line = 8
```

`hal/arm64/board.toml` gains four:

```toml
# The virtio-mmio transports: QEMU's `virtio-mmio-bus.N` is 0x200 bytes at
# 0x0A00_0000 + N * 0x200, on GIC interrupt 48 + N. A window is whole pages,
# so a row is the page of eight transports that starts at transport N, named
# for it and on its line; a device is placed only at a page's first
# transport, and the other seven stay empty.
[devices.VIRTIO0]
type = "virtio-mmio"
base = 0x0A00_0000
len = 0x1000
line = 48

[devices.VIRTIO8]
type = "virtio-mmio"
base = 0x0A00_1000
len = 0x1000
line = 56

[devices.VIRTIO16]
type = "virtio-mmio"
base = 0x0A00_2000
len = 0x1000
line = 64

[devices.VIRTIO24]
type = "virtio-mmio"
base = 0x0A00_3000
len = 0x1000
line = 72
```

Names: `base`, `len`, `line` as every row has them (`board.saw` l.1-60);
`type` names the hardware block at the address, the transport, because the
board cannot know what the emulator plugs into it. A later virtio-blk driver
declares the same type and its image binds it to another row.

The cases use `VIRTIO7` on riscv32 and the flat profile (QEMU's default
placement, 0x1000_8000, line 8) and `VIRTIO24` on arm64 (0x0A00_3000,
INTID 72), each with an explicit `bus=` (§6.3).

### 2.4 Probing or naming

**The image names the transport; the driver checks what is there.** Probing
`DeviceID` across transports would need every transport's window and line,
which breaks one process per device and gives a driver authority over
devices it does not drive. With several devices of one type on the board, the
builder already refuses an image that does not name one
(`tools/sosimage/src/imagefile.saw` l.1039-1041: "the board ... has N devices
of the type ...: name the one the need ... takes in `device`"), so the image
says:

```toml
[[process]]
name = "net"
package = "sawos:drivers/svc-virtio-net"
device = { net = "VIRTIO7" }
pools = { queues = "netq" }
on-exit = "shutdown"
```

The driver reads `MagicValue`, `Version` and `DeviceID`, in that order,
before any other register (§4.2.3.1.1: "The driver MUST start the device
initialization by reading and checking values from MagicValue and Version.
If both values are valid, it MUST read DeviceID and if its value is zero
(0x0) MUST abort initialization and MUST NOT access any other register").
It refuses, writing no register at all (§4.3):

- a `MagicValue` other than `0x74726976` (§4.2.2.2: "MUST ignore a device
  with MagicValue which is not 0x74726976, although it MAY report an
  error"); the window may not be virtio at all, so not even `Version` is
  read;
- a `Version` other than 2, a legacy transport (the driver ignores it, as
  §4.2.2.2 requires of an unsupported version, and MAY report it);
- a `DeviceID` of 0, no device;
- any `DeviceID` other than 1.

Each is a line and exit 1 (§4.3's table). **One deviation:** §4.2.2.2 says a
driver "MUST ignore a device with DeviceID 0x0, but MUST NOT report any
error". That rule is for a driver that probes transports and must stay quiet
about the empty ones. This driver probes nothing: the image bound it to this
transport, so an empty one is an image or emulator error, and the sawos rule
is never to hide one. It prints `SOS netdrv: no device at this transport`
and exits 1. That is a deviation from a MUST NOT, and it is the owner's to
accept (Q10).

### 2.5 What the declaration asks for, and what the image grants

`drivers/svc-virtio-net/process.toml`, in the UART drivers' shape
(`drivers/svc-uart-ns16550/process.toml`). It has a `[ceilings]` table,
because that is where a process asks for `physical` (§3.5), and a table is
complete when present (spec l.5136-5138: "a kind it does not list gets no
rights"), so it lists every right the driver spends:

```toml
format = 1
arena = 0x1000
ram = "fast"                # it services the transport's interrupt

[[boot]]
name = "system"
kind = "system"
tag = 1
rights = ["debug", "process-self"]

[[boot]]
name = "net"
kind = "window"
type = "virtio-mmio"
tag = 2

[[boot]]
name = "net"
kind = "line"
type = "virtio-mmio"
tag = 3

[[boot]]
name = "netdev"
kind = "serve"
protocol = "netdevproto"
tag = 4

# The pool its queues and every client's ring come from. Kept without
# `transfer` and `mint`, so no code of this process can share it.
[[boot]]
name = "queues"
kind = "memory"
tag = 5
rights = ["allocate", "map", "physical"]

[ceilings]
process = ["boot-handles", "thread-self", "exit", "waiter-create", "map"]
thread = ["control"]
waiter = ["attach", "wait"]
interrupt = ["wait", "ack"]
memory = ["transfer", "mint", "map", "physical"]
iomemory = ["map"]
mapping = ["unmap", "base"]
pool = ["allocate", "map", "physical"]
pipe-outlet = ["transfer", "take", "wait"]
pipe-request = ["reply", "wait"]

[quota]
mappings = 3                # the window, the queues, the client's ring
memories = 2                # the queues' allocation, the client's ring
waiters = 1
handles = 16
```

The `process` and `thread` rows hold what a program written with `main`
spends (spec l.5152-5154: `BootHandles`, `ThreadSelf`, `Exit`, and its main
thread's `Control`), which the builder already refuses a table for
withholding (`declaration.saw`, `check_runtime`). The rights each kind's enum
has are on main in `kernel/abi/src/rights.saw` (`ProcessRight` …
`PipeRequestRight`); the implementer confirms the `pipe-outlet` row against
what `Waiter.give(outlet:)` spends.

The image's entry grants the opt-in at its end (§3.5):

```toml
[[process]]
name = "net"
package = "sawos:drivers/svc-virtio-net"
device = { net = "VIRTIO7" }
pools = { queues = "netq" }
ceilings = { memory = ["transfer", "mint", "map", "physical"], pool = ["allocate", "map", "physical"] }
on-exit = "shutdown"
```

Every record kind exists today (`tools/sosimage/src/declaration.saw` l.1-45:
`window`, `line`, `serve`, `memory`; a `memory` record is bound to an image
pool by `pools`, `imagefile.saw` l.37-41), and the stock root gives each
(`root/bootplan/src/lib.saw` l.103-127; a `memory` record is given its pool's
row whole, `tools/sosimage/src/plan.saw` l.496-500, `Source.Row`). The
image's `ceilings` key is new (B1, §3.6).

**Handles, exactly, as the GPIO driver's comment counts them**
(`drivers/svc-gpio-esp32s3/process.toml`, `[quota]`): its records (five, the
outlet leaving at the attach: four), its Process, its main thread's Thread
handle, three Mappings and its Waiter make ten; a delivery needs room for the
obligation it mints and for every handle the request carries before it mints
any, so the most it holds is those ten, the one `WaitEvent` it holds, the
request being served, and the `PIPE_MSG_HANDLES` (4, `kernel/abi/src/ops.saw`
l.1550) that request carries: sixteen. A request's carried handles are
released as its bytes are copied out, before the driver acts on it (the
UART's pattern, `svc-uart-ns16550/src/main.saw` l.544-547), so while an
`Attach` is served the ring's `Memory` and the client's sibling of it (two)
stand where the carried handles stood. The driver releases each `Memory`
handle of its own once it has mapped the object, read `Physical`, and, for a
ring, minted and sent the client's handle; the rows keep the allocations. A
row short of sixteen lets a client's request end the driver.

`mappings = 3` fits riscv32's budget, four rows a child (`hal/riscv32/board.toml`
`[limits.default]` comment); arm64 gives 12. `memories = 2` counts the two
allocations the driver makes; a sibling is a handle, not an allocation.

### 2.6 The rules

- **One process per device:** each row is one device instance and is bound
  to one process; on arm64 the instance is the page, and the board's comment
  says the other seven transports in it stay empty.
- **Consistent names:** a range is `base` and `len` in the board, the pool's
  `len` in the image, `Mapping.len()` and `Physical`'s record in the API; a
  slot's length in the new `pktring` accessors is `len` (§4.2); the image's
  `ceilings` table uses the declaration's kind and right names; the case key
  that selects the peer's behaviour is `script`, as is the peer's flag (§6).
  The one older name kept is `pktring`'s `Layout.slot_bytes()`, an existing
  API.

---

## 3. DMA addressing, `Physical` and contiguous memory

### 3.1 What the device needs

Every descriptor carries the address the device masters the bus with (virtio
1.2 §2.7.5, `le64 addr`). §6's `ACCESS_PLATFORM` entry says that with the bit
clear "the device will always use physical addresses matching addresses used
by the driver (typically meaning physical addresses used by the CPU)", and
§6.1: "If VIRTIO_F_ACCESS_PLATFORM is not offered, then a driver MUST pass
only physical addresses to the device". QEMU's `virt` machines have no IOMMU
in front of virtio-mmio, so the address is the guest physical address.

With the ring allocated by the driver (§5.2, the owner's ruling), the device
sees exactly two kinds of object, both allocations from the driver's own pool
`netq`:

1. the queue memory (one allocation);
2. each attached client's ring (one allocation per `Attach`).

The driver allocates each with the `contiguous` flag (§3.4), so each is one
physical range by promise, and a base address and an offset are enough.

### 3.2 Per profile, before this unit

| Profile | Where a RAM row lands | What `Mapping.base()` answers | Physical address known today? |
|---|---|---|---|
| riscv32 (PMP, tier 2) | at its own address (spec l.3259-3266) | the user address, which "on a tier that does not translate ... is also the physical one" (spec l.1430-1440; `kernel/abi/src/ops.saw` l.868-871 on main) | yes: `base()` |
| riscv32-flat (tier 3) | the same | the same | yes: `base()` |
| arm64 (MMU, tier 1) | at the map cursor, inside the 4 MiB grant window from `RAM_BASE` (`hal/arm64/kernel/lib.saw` l.494-499 `map_place`, l.588 `map_target_ok`, l.2326 `USER_RAM_TOP`), backed by frames anywhere in RAM (l.527-529 `map_source_ok`) | a user address that is not the frame's | **no** |

On arm64 no op answers it, on purpose: "There is no op on a pool or an
allocation that reads its bounds" (spec l.1020-1031). The kernel has the
number: an allocation's slot records its physical base and length
(`kernel/core/objects.saw` l.1116-1122 on main, `MemorySlot.base`, `.len`;
"Addresses-as-data stay physical everywhere they are stored — Pool and Memory
capabilities", spec l.3285-3289). The driver uses `Physical` on every
profile, so it has one spelling; on riscv32 and the flat profile it answers
what `base()` would.

Device windows are not a problem: "A device window is mapped at its own
address on every profile" (spec l.1334; `map_place` answers `pa` for a
device, `hal/arm64/kernel/lib.saw` l.494-497).

### 3.3 The second arm64 problem: the device window

A process on arm64 can be granted device pages only inside the 2 MiB block
that holds the console: the per-process set has one device level-2 entry
and one device level-3 table (`hal/arm64/kernel/lib.saw` l.1104-1107,
l.1284-1285 `DEVICE_WINDOW_BASE = (UART_BASE >> BLOCK_SHIFT) << BLOCK_SHIFT`),
and `device_window_ok` refuses anything outside it (l.600-608), which
`IoMemoryOp.Map` answers as a `BadArg` fault (`kernel/core/dispatch.saw`
l.3598-3610). The console is at 0x0900_0000; the transports are at
0x0A00_0000, another block. So on arm64 the driver's `map(iomemory:)` faults
today. Unit K2 (§3.7) fixes it.

### 3.4 Unit K1: `MemoryOp.Physical` and `contiguous` (kernel)

As ruled. The numbers are re-checked on main (`so103a3/main`, `850c5849`):
`MemoryOp` has `Map = 0`, `Extend = 1` (`kernel/abi/src/ops.saw` l.741-744);
`MemoryRight` has bits 0, 1, 9, 10, 11 (`rights.saw` l.630-636); `PoolRight`
carries `Map`, `MapExecute` and `Extend` at `MemoryRight`'s numbers and starts
its own at bit 16 (l.668-675, l.681-689); `AllocateFrom` is `Any = 0`,
`Front = 1`, `Back = 2` in `Allocate`'s third word (`ops.saw` l.772-792,
`dispatch.saw` l.3529-3531).

**The op.** `MemoryOp.Physical = 2`. It takes the address and capacity of a
record in the caller's memory and writes two words there, the object's
physical `base` and its `len` (`MemorySlot.base` and `.len`, which `Extend`
raises), as `PoolOp.Available` answers its three words through a record
(spec l.1142-1144: "Three words do not fit the one value register, so the op
answers through a record as `PipeCreate` does"); `sosabi` publishes
`MEMORY_PHYSICAL_RECORD_WORDS = 2`. Its answers, in order:

1. a handle without `MemoryRight.Physical` is the `AccessDenied` fault;
2. a capacity below the record is the `BadArg` fault, and a destination the
   copy-out door refuses is its fault (`BadAlign`, `BadBuffer`);
3. an object that was not created contiguous answers the **`NoResource`**
   status, with nothing written: a holder that was given the object cannot
   know how it was made, so this is a status and not a fault (§5.7's rule),
   and `NoResource` is what `map` already answers for memory the kernel
   cannot serve as asked (Q13);
4. otherwise it writes the record and answers `Ok`.

**The right.** `MemoryRight.Physical = 1 << 12`, the next bit after `Extend`
(bit 11) and below a pool's own (bit 16). `PoolRight.Physical = 1 << 12`: a
pool carries it on behalf of what it allocates, as it carries `Map`,
`MapExecute` and `Extend`, with the static asserts `rights.saw` keeps for
each (l.681-700: "a case added to `MemoryRight` owes one here"). The kernel's
default sets:

| Set | Today (main) | With K1 |
|---|---|---|
| `memory_rights()` (boot allocations; the mask `Any`/`Back` allocations are cut with) | `Transfer \| Mint \| Map \| MapExecute` (l.1218-1222) | adds `Physical` |
| `pool_rights()` (boot pools) | `memory_rights() \| Extend \| Allocate` (l.1235-1238) | adds `Physical`, through `memory_rights()` |
| `front_allocation_rights()` (a `Front` cut, the heap's) | `Map \| MapExecute \| Extend` (l.1247-1250) | unchanged: a heap's memory is never given to a device |

The kernel must mint the bit at boot or nothing could ever hold it
(attenuation is monotonic, spec l.1216-1219); "off by default" is the
launcher's and the builder's job (§3.5), and root's own ceilings stay
unrestricted (spec l.1939). An allocation gets `Physical` only if the pool
handle carries it and the caller's `Memory` ceiling allows it: `Allocate`
mints `obj.rights & memory_rights()` through `mint_capped`
(`dispatch.saw` l.3545-3551), which caps by the ceiling.

**The flag.** `Allocate`'s third word becomes `from | flags`: bits 0-7 are
`AllocateFrom`, and `AllocateFlag.Contiguous = 1 << 8`. A word whose low
byte names no `AllocateFrom`, or with any other bit set, is the `BadArg`
fault, as an unknown `from` is today (`dispatch.saw` l.3529-3531). An object
made with the flag records it (`MemorySlot.contiguous`, a new field;
`objects.saw` l.1116 on), and so does every boot allocation, which is one
range by construction. The promise, in the spec: such an object is one
physical range now and in any future kernel; its `Extend` grows it in place
or answers `NoMemory`, which is all `Extend` does today (spec l.1387-1398).
Every allocation is contiguous today, so the flag changes no placement; it
changes what `Physical` answers, which is why it is not a no-op. The typed
`Pool.allocate(len:granularity:from:contiguous:)` takes it as a defaulted
`false`, as it takes `from` (`kernel/sysapi/src/pool.saw` l.114 on).

**The API.** `sos.Memory.physical() -> Result<PhysicalRange, SosStatus>`,
which reads the record into `PhysicalRange { base: UInt, len: UInt }`.

**The builder's minimum, in the same unit.** So that "off by default" holds
at every merge, K1 also: names `physical` in `tools/sosimage/src/rights.saw`
for `memory` and `pool` (the builder's tests hold that table to the kernel's
enums, l.1-10, so the name cannot land before the bit); gives every
stock-image child without `[ceilings]` a `memory` and a `pool` ceiling of
every right but `physical` (today such a child gets no ceiling rows at all,
`plan.saw` l.556-570), and every record with no `rights` a keep word without
it (`declaration.saw` l.510-525, `keep_word`, today `KEEP_ALL`); and refuses a
declaration that names `physical` ("the image grants it with `ceilings`,
which this builder does not read yet"). B1 lifts that refusal.

**The consumer sweep** (brief obligation 2: two default sets widen). The
runner's expectations print the old words, and each changes by 4096:
`SOS handleinfo: memory rights=1539` (`tools/sos_runner.py` l.5230),
`SOS ceilderived: pool rights=69123` (l.5323),
`SOS poolops: rights pool=69123 allocation=1539` (l.5468), the `poolleg` and
`cutrights` lines (l.5626, l.5910-5911) and their comments (l.5213, l.5310,
l.5448, l.5574, l.5902). Tests that name the default sets in comments:
`tests/map-exec-gated`, `tests/pool-ops`. The implementer greps for 1539 and
69123 in the runner and every expected line that prints a Memory or Pool
rights word. The builder's default adds a `memory` and a `pool` ceiling row to
every stock child's plan, so a stock case that prints a child's `Memory` or
`Pool` ceiling (`ProcessOp.Ceiling`) changes too, and root's arena, which the
builder sizes from the plan (`sosimage.plan.root_arena`), grows by those rows.

**Cases** (riscv32, riscv32-flat, arm64; each fails when its check is removed
or inverted):

| Case | What it pins |
|---|---|
| `memory_physical` | a contiguous allocation answers its base and length; on riscv32 and flat the base equals its Mapping's `base()`; on arm64 two rows of it at two addresses answer one base; a boot allocation answers |
| `memory_physical_not_contiguous` | an allocation made without the flag answers `NoResource`, and the record is untouched |
| `memory_physical_denied` | a sibling minted without `Physical` faults `AccessDenied` |
| `memory_physical_record` | a capacity of one word faults `BadArg` |
| `pool_allocate_flags` | a third word with bit 9 set, and one whose low byte is 3, fault `BadArg`; `Front \| Contiguous` and `Back \| Contiguous` are accepted |
| `pool_physical_capped` | a pool sibling minted without `Physical` allocates objects without it; a `Memory` ceiling without it does the same through a pool that carries it |
| `memory_contiguous_extend` | a contiguous `Front` allocation extends in place, then answers `NoMemory` past the frontier, and still answers `Physical` with the grown length |
| `stock_physical_default` | a stock-image child with no `[ceilings]` reads its `Memory` ceiling (`ProcessOp.Ceiling`) and its pool record's rights, and neither holds `physical` |

### 3.5 Off unless asked for: the declaration asks, the image grants (B1)

As ruled. The ruling's terms, then what the builder does with them.

- **The declaration asks** in its `[ceilings]`: `memory = [..., "physical"]`.
- **The image grants** on the process's entry, in a per-kind table
  `ceilings` with the same kind and right names:
  `ceilings = { memory = [..., "physical"] }`.
- **The image's table is the image's ceiling** for each kind it lists. The
  process's effective ceiling for a kind is the declaration's (or, with no
  `[ceilings]`, every right but `physical`) intersected with the image's; a
  kind the image does not list is left as the declaration says.
- **An opt-in right** (only `physical` today) must appear in both, or the
  build fails, naming the process, the kind and the right.
- **Refusals** (each names the process, the kind and the right):
  - an opt-in the declaration asks for and the image does not grant;
  - an opt-in the image grants and the declaration does not ask for;
  - an image list that removes a right one of the process's own records
    keeps (a `memory` record's keep word against the image's `pool` list, a
    `window`'s against `iomemory`, a `line`'s against `interrupt`, a
    `serve`'s against `pipe-outlet`, a `client`'s against `pipe-inlet`), so
    `map` cannot be stripped by accident;
  - an image list that removes a right the process's runtime spends (the
    rule `check_runtime` applies to a declaration today, spec l.5150-5159),
    applied to the effective ceiling;
  - an unknown kind or right, a right named twice, and a `system` key, as
    for a declaration.
- **The defaults change:** with no `[ceilings]`, a process gets every right
  except `physical`; a record with no `rights` keeps "every right except
  `physical`" (`keep_word`'s `KEEP_ALL` case); root's ceilings stay
  unrestricted.

**Which kinds carry the opt-in.** `physical` is a right of two kinds,
`memory` and `pool` (§3.4), and the ruling's "naming the process, the kind and
the right" is per kind, so the rules above apply to each kind separately. A
driver that allocates its DMA memory itself needs both: its `pool` record
must keep `physical` for its allocations to carry it, and its `memory`
ceiling must allow it for `Allocate`'s mint. Q12 asks the owner to confirm
reading the ruling this way.

**The plan.** The stock root already narrows a child's ceilings kind by kind
to the plan's words (`root/src/main.saw` l.18-20, l.380-400, `restrict`) and gives each record with its keep word, so B1 changes
what the builder writes and nothing in root: each child's plan gets a ceiling
row for every kind whose effective word is not all ones (at least `memory`
and `pool`, since every child now lacks `physical` unless granted), and each
record its keep word within the effective ceiling. A child that is granted
`physical` gets it through its pool record's keep word and its `memory`
ceiling; the heap pools root gives keep `HEAP_POOL_KEEP`, which has no
`physical` (`root/src/main.saw` l.84-85), so a heap is never DMA memory.

**The client's ring is not covered by any of this.** A ceiling caps what a
process obtains from the kernel and not what it is sent (spec l.1893-1902; l.1901: "A ceiling therefore bounds what a process can obtain from the kernel, not what it can be given").
The ring reaches the client in a reply, so the driver mints the client's
handle without `Physical` itself (§5.2), and a case pins it (§7.3).

### 3.6 Where the image `ceilings` table and the default changes belong

| | Inside the networking track | **A general builder unit (B1), filed under the limits issue** |
|---|---|---|
| What changes | every stock image's children's ceilings and record keep words, the image format, spec §12 | the same |
| Who it serves | one driver today | every future opt-in right and any image that wants to narrow a package's ceilings, which the image format cannot say today (spec l.5159-5160: "An image cannot widen or narrow a declaration's ceilings") |
| Review | a format change hidden in a driver unit, reviewed by readers looking at virtio | reviewed as a packaging change, with SO-95's format rules and the per-image limits work it sits beside |
| Tests | its refusals would be tested through a network image | builder tests in `tools/sosimage/tests`, plus one stock-image case that asks, is granted and calls `Physical`, with no network involved |
| Dependency | networking waits on itself | networking (A3-4) depends on B1; B1 depends on K1 |

**I'd do the general unit, as you lean.** The image `ceilings` table is a
packaging feature in the family of per-image limits (an image adjusting what a
package declares, within what the board allows), it changes the defaults of
every stock image, and its refusals are about formats, not devices. What stays
in the networking track is the driver's declaration and image entry (§2.5).
The one piece that cannot wait for B1 is the default "every right but
`physical`", because K1 makes the kernel mint the bit at boot, and between a
K1 merge and a B1 merge every stock child would otherwise get it; so that
piece rides with K1 (§3.4), and B1 brings the image table, the
intersection, the opt-in and the refusals.

B1's spec text is §10's §12 rows. Its files: `tools/sosimage/src/imagefile.saw`
(the `ceilings` key in `[[process]]`, l.604 `known` list, and its reading),
`declaration.saw` (effective ceilings, the opt-in set, `keep_word`),
`plan.saw` (`ceiling_rows` and keep words from the effective ceiling,
l.556-580), `rights.saw` (the opt-in set), the builder's tests, `spec.md`
§12, and runner cases.

### 3.7 Unit K2: a second device window on arm64

`hal/arm64/kernel/lib.saw` keeps one device level-3 table per 2 MiB block it
offers, and offers two: the console's block (0x0900_0000) and the
transports' (0x0A00_0000).

- The device level 2 gets a second live entry; `PT_SET_DESCRIPTORS` grows by
  one table (l.1152-1156): 4 KiB a set, 16 sets (`PROT_DOMAIN_SLOTS`,
  l.1183), 64 KiB of `.bss`, which costs no image bytes (l.1176-1180);
  `virt.ld`'s `_bss_end` assert is what checks it fits under root's region.
  The one-table alignment slack still covers the pool (l.1211-1215).
- `device_window_ok`, `prot_device` and the per-set build take the window
  from a two-entry list instead of `DEVICE_WINDOW_BASE`.
- `hal/arm64/kernel/ABI.md` l.46 and l.165-173 ("a level-2 table whose one
  live entry is a level-3 table over the console's 2 MiB block") and the
  translation-layout table (l.252) say two blocks.

Nothing in spec.md names the one block; spec §5b's arm64 row does not change.

### 3.8 What the driver does with it

At start: allocate the queue memory from `netq` with `contiguous`, map it,
read `Physical`; every queue address the driver writes to the device is
`base + offset` from `Physical`'s record, every CPU access the Mapping's
`base() + offset`. At `Attach`: allocate the ring from `netq` with
`contiguous`, map it, read `Physical`; a slot's bus address is
`base + Layout.slot_offset(ring, slot)` (`pktring` l.257-266). The offsets
come from the driver's private layout and checked indexes (§4.2), never from
shared memory. The driver gives the device no address outside its pool's
allocations: no header, buffer or ring lives in its image, stack or arena.
§4.5 depends on that rule.

---

## 4. Queues and buffers

### 4.1 The queue memory

One page-sized allocation (0x1000) from the driver's pool (`memory` record
`queues`, image pool `netq`, which also holds the client's ring, §7.1), made
with `contiguous`, mapped read-write into the driver and zeroed by it
(§4.2.3.2 step 4: "Allocate and zero the queue memory"; spec §2.5 promises no
zeroing).

**Queue size.** `MAX_QUEUE = 64`. A queue's size is the largest power of two
at most min(`QueueNumMax`, `MAX_QUEUE`), since a split queue's size is always
a power of two (§2.7) and `QueueNum` may not exceed `QueueNumMax`
(§4.2.2.2). Below 2 the queue is refused (§4.2.3.2 step 3: 0 means the queue
is unavailable; a one-descriptor queue holds no chain). A `QueueNumMax` of
256 (QEMU's, exp2) gives 64; 48 gives 32; 1 is refused.

Layout at 64, offsets from the first 16-aligned byte of the allocation (on
riscv32 an allocation is only 4-aligned, the grain, so the driver rounds the
physical base up; 16 bytes of the page are slack). A smaller queue packs the
same areas smaller:

| Area | Alignment (§2.7 table) | Offset | Bytes |
|---|---|---|---|
| RX descriptor table | 16 | 0 | 16 × 64 = 1024 |
| RX available ring | 2 | 1024 | 6 + 2 × 64 = 134 |
| RX used ring | 4 | 1160 | 6 + 8 × 64 = 518 |
| TX descriptor table | 16 | 1680 | 1024 |
| TX available ring | 2 | 2704 | 134 |
| TX used ring | 4 | 2840 | 518 |
| RX headers, one a chain | 2 | 3360 | 12 × 32 = 384 |
| TX header, shared, zero | 2 | 3744 | 12 |
| end | | 3756 | under 4096 − 16 |

Chains are fixed pairs: chain `c` is descriptors `2c` (header) and `2c + 1`
(frame), so half the queue's size in frames can be in flight per direction
(32 at 64), and a used element's `id` must be an even number below the
queue's size naming a chain in flight. The driver writes each `addr` and
`len` once per posting; flags are `NEXT` and `WRITE` for an RX header,
`WRITE` for an RX frame, `NEXT` for the TX header, 0 for a TX frame (§2.7.5).

Queue setup is §4.2.3.2: select, check `QueueReady` reads 0, read
`QueueNumMax`, write `QueueNum`, the three address pairs, `QueueReady` 1. RX
is queue 0, TX queue 1 (§5.1.2).

### 4.2 Zero copy, the ring the driver owns, and rule 5.2

§5.3 of the networking design says "the driver points descriptors at the
shared slots". That holds with rule 5.2 (networking.md l.912-929), because
the driver never acts on a slot's bytes: it acts on index words and length
words only, and the device, not the driver, reads or writes the bytes. With
the reversed `Attach` (§5.2) the ring is the driver's allocation, but the
client still writes half its words, so the ring is still input to the driver.

**What the driver reads from the ring, once each, and checks:**

| Field | Read | Check | On failure |
|---|---|---|---|
| ToServer head | at a `Tx` doorbell and when TX room frees | below `2n`, and in order (`pktring` `poll`, l.732-754) | ring fault: `IndexPastCount` or `IndexOutOfOrder` |
| a ToServer slot's length word | once, when the driver takes the slot | at most the slot size (`LengthPastSlot`, as `receive` l.773-776) | ring fault |
| the same length, as a frame | from the private copy | at least `ETHERNET_HEADER_BYTES` (14) and at most 14 + the reported MTU | not a ring fault: the frame is not sent, `tx_errors` counts it, and the slot completes as sent |
| ToClient tail | at an `RxReturn` doorbell and when the driver looks for free RX slots | below `2n`, and in order (`read_out_tail`, l.652-665) | ring fault |
| a doorbell's range | on the wire, then against the attached ring | `netdevproto`'s decoder refuses a range no ring can hold (`range()` l.486-491, `SlotRange.fits(MAX_SLOTS)`); the driver then checks it against the attached ring's own count (`SlotRange.fits(n)`, `pktring` l.333-336), which netdevproto's status list promises ("a range that does not fit the ring", l.61-67) | `BadArg` either way; the range is never acted on, since the index words say the same or more |

The descriptor's address is computed from `Physical`'s base, the driver's
private layout and the checked slot number; its length is the checked private
copy. Nothing the client writes can move a descriptor outside its own ring,
and a client that rewrites a slot's bytes while the device reads them changes
only its own frame, which it could have sent anyway.

**The ring is input to the client too.** The client checks what it is given
as a server checks a client's ring today (`pktring` doc l.69-94): the handle
holds `Map` (`rights()`), `map` may answer `NoResource`, and the mapping's
`len()` must hold the layout (`SharedWindow.init(at:len:layout:)`,
`RingError.ShortMemory`). A driver is in the TCB, but the client's
transcript should name a broken one rather than fault.

**What the ring's ownership buys, and what holds anyway:**

- The client can write into a ToClient slot the device is filling. That
  corrupts only frames sent to that client.
- The client can die with RX buffers posted. The ring is the driver's
  allocation, which the driver's row and handle keep out of the pool whatever
  the client does; the driver resets the device and waits for `Status` 0
  before it unmaps and releases (§5.4). "A device MUST NOT send notifications
  or interact with the queues after indicating completion of the reset by
  reinitializing device status to 0, until the driver re-initializes the
  device" (§2.4.1); "The driver SHOULD consider a driver-initiated reset
  complete when it reads device status as 0" (§2.4.2). QEMU's soft reset
  clears each queue's `enabled` (`virtio-mmio.c` ~l.72-82, ~l.370-395).
- The driver can end a connection while the client still maps the ring (a
  ring fault, §5.4). The allocation then stays out of `netq` until the
  client's row goes, since a row counts against its allocation (spec §2.5
  l.1006-1013, l.1527-1531: "a range is never handed out again while a domain
  still grants it"). So the driver cannot hand a client's still-mapped ring
  to the device as a new client's, nor to anyone.
- The client cannot donate the ring to the kernel: `SlabDonate` needs the
  only reference and no row (spec l.2286-2290), and the driver holds a row.
- The client's handle carries `Map` and `Transfer` only (§5.2): not
  `Physical`, so the client never learns where the ring lies, and not `Mint`.
- The driver can die with buffers posted: §4.5.

**`pktring` for a ring the server lays out (unit A3-1).** Today the client
lays the ring out and stores all four index words (`Endpoint.client`,
l.585-592), and the server stores only its own two (`Endpoint.server`,
l.596-601). With the reversed `Attach` the server lays it out, and the
client must not store a word the server may already have published (the
driver posts RX buffers before it answers `Attach`, and a frame could be
published before the client's first instruction). Two constructors, beside
the existing pair, which the other packet protocols keep:

```saw
extension Endpoint<M: Shared> {
    /// The server's end of a ring it allocated and lays out itself: every
    /// index word is stored zero, so the ring starts empty. The server does
    /// this before it gives the memory to the client.
    public static func server_laid_out(memory: M, layout: Layout) -> Endpoint<M>

    /// The client's end of a ring the server laid out and gave it. It stores
    /// nothing, and trusts nothing the memory holds.
    public static func client_given(memory: M, layout: Layout) -> Endpoint<M>
}
```

**The in-place accessors (unit A3-1).** `Endpoint` copies today (`send`
l.683-712, `receive` l.763-791). The server side of a zero-copy driver needs
the same checks without the copy, and with release in completion order:

```saw
/// A slot named in place: its number, its offset in the ring's memory, and
/// its length as read once and checked.
public struct SlotRef {
    public slot: UInt32,
    public offset: UInt,
    public len: UInt,
}

extension Endpoint<M: Shared> {
    /// The next filled slot of the ring this end drains, without copying it:
    /// its length word read once and checked against the slot size. It is
    /// `receive` without the copy, and moves the same cursor.
    public func take(&var self) -> Result<SlotRef, RingError>

    /// Gives back the oldest `count` slots taken (by `take` or `receive`) and
    /// not yet released, and answers them for the doorbell. More than are
    /// taken and unreleased is a caller's bug, and panics.
    public func release_taken(&var self, count: UInt32) -> Result<SlotRange, RingError>

    /// The next free slot of the ring this end fills, without writing it:
    /// the peer's tail read afresh only when every slot it last freed is
    /// reserved. `Full` when none is free.
    public func reserve(&var self) -> Result<SlotRef, RingError>

    /// Writes `len` as the length word of the oldest reserved slot not yet
    /// filled, marking it filled; `publish` then shows it to the peer.
    /// `len` past the slot size is `TooLong`; no reserved slot is a caller's
    /// bug, and panics.
    public func fill(&var self, len: UInt) -> Result<UInt32, RingError>
}
```

**Cursor rules.** The drain side has one family's cursors, shared: `take`
and `receive` both advance the taken cursor (`in_tail`), and differ only in
whether they copy; `release()` gives back every slot taken since the last
release, `release_taken(count:)` the oldest `count` of them, and both store
the same tail word. Mixing them is coherent. The fill side gains one private
cursor, `out_reserved`, past the slots reserved: `reserve` advances it, `fill`
advances `out_head` toward it, and `publish` publishes up to `out_head` as
today. `send` writes the slot at `out_head`, which while any slot is reserved
and not filled is a slot the device may be writing, so `send` on an
`Endpoint` with such a slot is a caller's bug and panics; with none, `send`
and `reserve` interleave correctly, since both start at `out_head`. `take`
latches and answers the same errors as `receive`; `reserve` the same as
`send`'s room check. `SlotRef.len` is 0 from `reserve` (the device writes the
bytes; the driver states the length at `fill`).

Hosted tests in `drivers/pktring/tests/input.saw` hold each to rule 5.2 as
A1's tests hold `receive`: a length word changed between two reads is read
once; an index past the count; out of order; `release_taken` past what is
taken panics; `send` with a reservation outstanding panics; `fill` with
none panics; `take` then `receive` then `release()` releases both;
`client_given` stores no word (a fake `Shared` records stores), and
`server_laid_out` stores all four.

### 4.3 The device is checked too, and how the driver gives up

The device is QEMU here, but a driver that trusts a used ring blindly would
let a buggy device steer it.

| Device value | Check (virtio 1.2) |
|---|---|
| `MagicValue`, `Version`, `DeviceID` | §2.4 above |
| offered features | `VERSION_1` and `MAC` present; `ACCESS_PLATFORM` absent (§1.2) |
| status after `FEATURES_OK` | the bit still set (§3.1.1 step 6, §2.2.2) |
| `QueueReady` before setup | 0 (§4.2.3.2 step 2) |
| `QueueNumMax` | at least 2 (§4.1) |
| `mtu`, when `MTU` is negotiated | at least 68 (§5.1.4.1) |
| `ConfigGeneration` | equal before and after reading `mac`, `status` and `mtu`; read again on a change, at most `CONFIG_READS` (8) times, then refused by name (§2.5.1, §4.2.2 `ConfigGeneration` at 0x0fc) |
| `Status` after a reset | reads 0 within `RESET_READS` (64) reads (§2.4.2) |
| used `idx` | advanced by no more than the chains in flight (`idx` is mod 2^16) |
| used element `id` | even, below the queue's size, a chain in flight (§2.7.8) |
| RX used `len` | at least 12 and at most 12 + the slot size (§2.7.8.2: the device "MUST write at least len bytes"; a value past the buffer is a device bug) |
| RX header `num_buffers` | 1 (§5.1.6.4.1) |
| `DEVICE_NEEDS_RESET` in status at a configuration change | fatal, by name (§2.1.2) |

The two bounds count reads, not time: QEMU completes a reset inside the
`Status` write and its configuration never changes under a read, so on the
emulator the first read answers. A device that never settles is refused by
name instead of hanging the driver.

An RX frame shorter than 14 bytes is not a device fault: it is published as
received and the client's stack drops it (`netstack` ether.saw l.50-52,
`Drop.EtherRunt`). The driver judges no frame's content.

**Register widths.** The control registers of Table 4.1 are read and
written 32 bits wide and aligned; config space is read at each field's own
width, `mac` a byte at a time and `status` and `mtu` 16 bits wide (§4.2.2.2:
"The driver MUST only use 32 bit wide and aligned reads and writes to access
the control registers described in table 4.1. For the device-specific
configuration space, the driver MUST use 8 bit wide accesses for 8 bit wide
fields, 16 bit wide and aligned accesses for 16 bit wide fields"). The
hosted library's `Registers` trait has exactly those accessors (§7.4).

**How the driver gives up.** One table, by when the failure is found:

| Class | Found | Registers it writes | Exit | Machine status |
|---|---|---|---|---|
| not a network device here | `MagicValue`, `Version` or `DeviceID` (§2.4) | none (§4.2.3.1.1, §4.2.2.2) | 1 | 1 |
| refused at setup | a feature (§1.2), `FEATURES_OK`, `QueueReady`, `QueueNumMax`, `mtu`, `ConfigGeneration` | `Status` with `FAILED` (128) added (§2.2.1: "otherwise MUST set the FAILED device status bit and cease initialization"), then a reset (`Status` 0) and the bounded wait, since a queue set up before the failure may already be live | 1 | 1 |
| a device fault in service | §4.3's used-ring rows, `DEVICE_NEEDS_RESET` | `FAILED`, then a reset and the bounded wait, then unmap | 3 | 3 |
| a reset that does not complete | the bounded wait | nothing more; it unmaps nothing | 4 | 4 |
| the connection ended by a client fault | §4.2's ring rows (an `Attach` the pool cannot hold is a status, `NoMemory`, and ends nothing) | a reset and the bounded wait, then unmap | 2 | 2 |
| the client gone | the terminal level (§5.4) | a reset and the bounded wait, then unmap | 0 | 0 |

The machine status is the driver's exit code because the image says
`on-exit = "shutdown"` for it (`root/src/main.saw` l.43-45).

**Ordering.** The order of the driver's own work: descriptors and ring
entries written, a barrier, `avail.idx` written, a barrier, then the
`used.flags` read, then `QueueNotify` with the queue's index if `flags` is 0
(§2.7.13 "Supplying Buffers to The Device"; §2.7.13.4.1: "The driver MUST
perform a suitable memory barrier before reading flags or avail_event, to
avoid missing a notification"; §2.7.10.1, without `EVENT_IDX`: "If flags is
1, the driver SHOULD NOT send a notification. If flags is 0, the driver MUST
send a notification"; §4.2.3.3). A `flags` read before that barrier could be
stale and skip a notification the device needed. On the used side:
`used.idx` read, a barrier, then the used elements (§2.7.14).

The barrier is a two-line C file per architecture in the driver's `native`
(`fence iorw, iorw` on riscv32, `dsb sy` on arm64), which are also the
hardware-device barriers `ORDER_PLATFORM` asks for (§6.1). Saw's `Device`
accesses are volatile and "Volatile is not atomic — it constrains the
compiler, not the memory system; ordering across accesses still needs
fences" (sawlang LANGUAGE_SPEC.md l.17246-17252), and the runtime offers no
fence a process can call. The hosted library takes the barrier as a trait
method, so its tests check where it is called.

### 4.4 Interrupts

One task owns the Interrupt (spec §9 l.4413-4416). On its delivery:

1. read `InterruptStatus`;
2. write `InterruptACK` with the bits read masked to the two defined, bit 0
   (used buffer) and bit 1 (configuration change) (§4.2.2.2: "The driver
   MUST ignore undefined bits in InterruptStatus. The driver MUST write a
   value with a bit mask describing events it handled into InterruptACK ...
   and MUST NOT set any of the undefined bits in the value"; §4.2.3.4.1);
   an undefined bit is neither acked nor acted on, and does not keep the
   driver looping;
3. on bit 1, re-read `status` under `ConfigGeneration`; a changed `LINK_UP`
   bit queues a link event; `DEVICE_NEEDS_RESET` is fatal (§4.3);
4. drain both used rings (whatever bit 0 said): TX completions, then RX
   completions, publishing filled RX slots in ring order;
5. post RX buffers into every ToClient slot the client has freed, notify
   RX if any were posted and `used.flags` allows (§4.3);
6. answer the held `WaitEvent` if an event is pending;
7. `irq.ack()`.

Every device register access and used-ring read is before the ack (spec §9
"Ack is a release", l.4403-4410). A completion after step 2 sets the line
again on both machines (§2.1), and the kernel delivers it after step 7, so
none is lost.

Out of order: QEMU completes in order, but nothing promises it without
`IN_ORDER`. TX slots are released in ring order (`release_taken(count:)` over
the completed prefix); RX slots are filled and published in ring order
(`fill` for the completed prefix). A flag per chain is the bookkeeping.

### 4.5 The driver's death, and the bytes its device still addresses

**What happens.** A dead driver's teardown masks its line (spec l.2650: the
Interrupt's release "unbinds and masks the line"), releases its handles,
then clears its domain (spec l.1536-1545). Root hears of the death at the
start of the teardown, but "the whole teardown below runs to completion
first" (`kernel/core/sched.saw` l.655-664), and only then can root stop the
machine. So `on-exit = "shutdown"` acts after the frees, not before them,
and the device may still hold the queue addresses and, in posted RX chains,
slot addresses in a client's ring.

**Why that is safe in a stock image.** Since the owner's ruling on the ring,
every byte the device can address is in one pool, `netq`: the queue
allocation and each client's ring are the driver's allocations from it
(§3.1, §3.8). The argument is about that one pool:

1. **One holder, by the image.** The builder refuses a pool bound twice
   ("the pool ... is bound twice: a pool is bound once",
   `tools/sosimage/src/imagefile.saw` l.462 and l.483), and the plan gives a
   `memory` record its pool's row whole (`Source.Row`, `plan.saw` l.496-500),
   so root keeps no handle on it. `netq` is the driver's alone.
2. **One holder, by capability.** The driver's `queues` record keeps
   `allocate`, `map` and `physical` and not `transfer` or `mint` (§2.5), so no
   code in the driver can give the pool or mint a sibling of it: the rights
   are not there to spend (spec §3). What the driver gives clients are
   siblings of single allocations (§5.2), never the pool.
3. **A boot pool with no holder returns its bytes nowhere.** "When its last
   handle has gone and the last allocation from it has come home, its slot
   frees ... and its bytes go nowhere: a pool minted at boot has no pool
   above it" (spec l.1221-1230). The driver's death releases its `netq`
   handle; each allocation comes home when its last row and handle go, the
   queue allocation at the driver's domain clear, a ring when its client's
   row goes too; then the pool frees and its bytes are on no list.
4. **Until then, a still-mapped ring is its client's view only.** A ring a
   live client maps stays out of the pool (§4.2), so the device writing into
   it reaches only that client's frames.
5. **Nothing else reuses physical RAM.** Every process's region and
   destination is a boot allocation, which "came from no pool, and when it
   frees its bytes go nowhere" (spec l.1246-1249); kernel memory is its image
   and `.bss`; slab donations come only from an allocation with no other
   reference and no row (spec l.2286-2290).

So no process and no kernel object is ever given a byte the dead driver's
device still addresses. Revision 2's argument also needed the client's pool;
this one does not, and step 2 moves the last piece from "the driver's code
does not share the pool" to "the driver cannot". `on-exit = "shutdown"` is
then not what makes the death safe. It stays because a dead network driver
leaves nothing for the image to do, and so that a driver death is an end the
transcript reports (Q5).

**Where the argument stops holding,** and critical processes (or more) are
needed: a launcher that keeps a sibling of the driver's pool, gives it to two
processes or gives it with `Transfer` or `Mint` (a hand-written root may); a
driver that is restarted into the same pool; sub-pools and revoke (spec §2.5
l.1576 on, not built); anything that returns a boot pool's bytes to a live
pool. The builder's one-binding rule and the record's keep word are what make
it true in a stock image, so the argument is written into `imagefile.saw`'s
comment on that refusal and into the spec (§10).

**Critical processes, priced (A3-K3, optional).** Spec l.1567-1572 names
"critical processes — a process root marks at launch whose exit for any
reason faults the system, checked at the top of teardown ahead of any
release" as the second prerequisite of its userspace-IOMMU architecture, not
yet built. Built for A3 it would be:

- kernel: `ProcessOp.MarkCritical` on a new `ProcessRight.MarkCritical`,
  valid only on a process not yet started (`BadState` after), a flag in the
  process record, and at the top of `teardown_process`, before
  `notify_ready` and any release, a critical process stops the machine with
  `SOS: critical process ended: process=N` and a status;
- spec §2.7, §8 and the DMA paragraph's text; sysapi
  `Process.mark_critical()`;
- builder and root: an image key `critical = true`, a plan field, root
  calling the op before `start`;
- cases on all three profiles: a critical child that exits, faults and is
  killed stops the machine before root's `ended` line, and a non-critical
  sibling does not.

About an M unit, touching `kernel/abi`, `kernel/core` (`sched.saw`,
`dispatch.saw`, `process.saw`), `kernel/sysapi`, `tools/sosimage`
(`imagefile.saw`, `plan.saw`), `root/`, `spec.md` and the runner. It is not
needed for A3's images by the argument above; it is needed by the first
image where that argument fails. Recommended: A3 goes ahead on the argument,
with critical processes scheduled when restart or sub-pools are (Q5). If the
owner prefers the general mechanism now, A3-K3 comes before A3-4.

### 4.6 Cache coherence

- The queue memory and the client's ring are ordinary cacheable RAM rows:
  on arm64 a granted RAM page is `Normal`, write-back read/write-allocate,
  inner shareable (`hal/arm64/kernel/lib.saw` l.1295 `ATTR_SH_INNER`,
  l.1335, l.1347 `MAIR_ATTR_NORMAL`); riscv32 has no cache attributes in its
  PMP rows.
- The device is coherent with the CPU on both machines: on arm64 by the
  device tree, where every `virtio_mmio` node carries `dma-coherent`
  (`a64.dts`); on riscv32 by QEMU's model, which has no cache between a
  device's DMA and guest RAM (the riscv32 transport nodes carry no
  `dma-coherent`, and none is needed for QEMU's own devices).
- So the barriers of §4.3 order the accesses and the driver does no cache
  maintenance.
- A board whose bus master is not coherent needs a clean of the buffers and
  rings before each notification and an invalidate before each used-ring
  read, which this driver does not do. A virtio driver for such a board is
  a different design.

---

## 5. The netdevproto server

The driver is a completion-queue server like the UART drivers
(`drivers/svc-uart-ns16550/src/main.saw` l.1-45): the outlet is given to the
Waiter (consuming, persistent), the Interrupt and a held `WaitEvent`
obligation are attached, and every delivery arrives through `wait`.

### 5.1 Start

Map the window (`map(iomemory:)`), check the transport (§2.4), reset and wait
until `Status` reads 0 (bounded, §4.3), set `ACKNOWLEDGE` and `DRIVER`,
negotiate (§1.2), `FEATURES_OK` and check it; allocate the queue memory from
`netq` with `contiguous`, map it, read `Physical`, release the `Memory`
handle, zero the queues and set them up; read `mac`, `status` and `mtu`
(§4.3's widths), set `DRIVER_OK` (§3.1.1). The RX queue is empty until a
client attaches: its buffers are the ring's slots. QEMU holds frames that
arrive with no buffer posted (`virtio_net_has_buffers` returns 0,
`virtio-net.c` ~l.2010), and the stream backend stops reading its socket, so
nothing is lost before `Attach`.

A `Physical` that answers `NoResource` here, for an allocation the driver
made contiguous, is a kernel bug, and the driver stops with a line (exit 1,
the setup class of §4.3's table, after `FAILED` and a reset).

Two lines, through the debug print (no console record, as the UART drivers):

```
SOS netdrv: virtio-mmio version 2, network device, features version_1 mac status
SOS netdrv: mac 52:54:00:12:34:56, link up, mtu 1500, queues 64 and 64
```

(Each negotiated optional feature is named: `status`, `mtu`,
`order_platform`. Without `STATUS`, `link up` reads `link assumed up`.)

### 5.2 The ops

`Attach` is reversed, as ruled: the client sends slot counts and no memory,
and the driver answers with the ring.

| Op | What the driver does |
|---|---|
| `Attach(to_server, to_client)` | a request carrying a handle is `BadOp` (the decoder's new rule, §5.5). The layout's `bytes()` rounded up to the pool's granularity (`Pool.available`'s set, spec §2.5) is allocated from `netq` with `contiguous`; `NoMemory` (the pool cannot hold it) is answered with the new status `NoMemory`, and the connection stays (the client may ask for fewer slots). The driver maps the allocation read-write, reads `Physical`, lays the ring out (`Endpoint.server_laid_out`, §4.2), mints the client's handle with `Memory.mint(rights: Map \| Transfer)` (no `Physical`, no `Mint`), releases its own `Memory` handle (§2.5's count), posts RX buffers into the free ToClient slots (as many as there are RX chains), notifies, and answers `AttachInfo` (mac, the reported MTU, the request's counts) with the client's handle in reply handle slot 0 (`PipeRequest.reply_with`, `kernel/sysapi/src/pipe.saw` l.1446-1458 on main). The next `WaitEvent` is answered at once with the link's state. A second `Attach` is `Attached` |
| `Tx(range)` (a tell) | the range checked against the ring (§4.2); `poll` the ToServer ring; for each new slot while a TX chain is free: `take`, check the frame length (§4.2), post or complete with `tx_errors`; notify. Slots past the chains in flight wait in the ring until chains free. Answered `Ok` (and discarded, as the UART's `Write`) |
| `RxReturn(range)` (a tell) | the range checked; `reserve` every freed ToClient slot while an RX chain is free, post, notify |
| `WaitEvent` | answered at once if an event is pending; otherwise held, with a one-shot attach so an abandoned claim is a delivery (the UART's held `Read`, l.556-584). A second while one is held is `Busy` |
| `Stats` | `NetdevStats`: `rx_frames` (frames published to the client), `rx_dropped`, `tx_frames` (TX chains the device used), `tx_errors`, `ring_faults` |

An `Attach` whose reply does not land (the client dropped its claim) has
still made a ring. The driver treats it as attached; the client's next
request, or the connection's end, settles it. Since the only process that
could have attached is the one that dropped the claim, a client that does
this has broken its own connection, and the driver's state stays consistent.

**Events.** Pending state is a link flag, the ToClient range published since
the last report, and the ToServer range completed since the last report. One
`WaitEvent` answers one, in this order: a link change, `RxReady`, `TxDone`.
Frames that arrive while none is held stay published in the ring, and the
next `RxReady` names all of them (netdevproto l.47-51).

**`rx_dropped` is 0 on this device.** With zero copy the device has nowhere
to put a frame when the client holds every slot, so QEMU keeps it and stops
reading the stream: back-pressure reaches the peer instead of a drop. The
counter stays in the protocol for drivers that copy (the radio, §5.3 of the
networking design).

### 5.3 Link

With `STATUS`, link state is the `LINK_UP` bit (§5.1.4), re-read at each
configuration-change interrupt. QEMU raises one only when the value changes
(`virtio_net_set_link_status` ~l.720-735, `virtio_notify_config`). The
NIC's bit follows QMP `set_link`, not the stream's connection: it read
`0x0001` while the stream was still `connecting` (exp3), which is why the
runner starts the guest only once the stream is connected (§6.3). Without
`STATUS` the driver reports `LinkUp` and never `LinkDown`.

### 5.4 A full ring, a dead client, a fault

- **Full TX ring:** the client's own condition (`Full` from its `send`); it
  waits for `TxDone`. Nothing reaches the driver.
- **Full RX ring:** the driver has no slot to post; frames wait in QEMU
  (§5.2).
- **Dead client:** its last inlet gone, the outlet's terminal level arrives
  (the UART's `Readable`). The driver writes `Status` 0, reads `Status` until
  it reads 0 (at most `RESET_READS`), then unmaps the ring, prints, and exits
  0:

  ```
  SOS netdrv: client gone: device reset, ring unmapped
  SOS netdrv: rx=4 tx=5 tx_errors=0 ring_faults=0
  ```

  Reset, then `Status` read as 0, then unmap: the device has stopped using
  the queues before the last row on the ring goes and the allocation returns
  to `netq` (§4.2). A reset that does not complete is exit 4 with the ring
  still mapped (§4.3). The hosted tests pin the order with a fake that
  completes its reset late (§7.4).
- **Ring fault:** netdevproto says the connection ends (l.69-82). The driver
  has one outlet, so ending the connection is releasing it: every claim then
  answers `PeerClosed`, which is what a dead server looks like (§5.4 item 4
  of the networking design). The driver then resets, waits for `Status` 0
  and unmaps as above (the client may still map the ring, which then stays
  out of `netq` until the client's row goes, §4.2), prints the fault by
  `RingError.describe()` with its value and `ring_faults=1`, and exits 2. In
  an image where the driver says `on-exit = "shutdown"` that ends the image
  (Q6).
- **A client that drops its `WaitEvent` claim:** the abandoned delivery
  discards the obligation; the next `WaitEvent` is served (the UART's
  `Abandoned`, l.520-532).

### 5.5 `netdevproto`, reversed (unit A3-1)

The package's doc (`drivers/netdevproto/src/lib.saw` l.1-82) and its wire
change as follows; the hosted tests in `drivers/netdevproto/tests/` follow.

- **The table:** `Attach` | `1` | `to_server u16, to_client u16` | `mac [6],
  mtu u16, to_server u16, to_client u16; handle slot 0: the ring`. The
  request carries no handle; the reply carries one.
- **`Attach`'s doc**, replacing l.37-43: "a call, the connection's first
  request. The client sends the two slot counts it wants. The driver
  allocates the ring from its own memory, one physical range, lays it out
  (`Endpoint.server_laid_out`), and answers the device's MAC address, its
  MTU, and the slot counts, which are the request's, with the ring's Memory
  in handle slot 0, carrying `Map` and `Transfer` and nothing else. The
  client checks the handle as input (`pktring`'s rules for a ring it is
  given: `Map` held, the mapping no shorter than the layout) and takes its end
  with `Endpoint.client_given`. The ring is the driver's: it does not return
  to the driver's memory while the client maps it."
- **A new status**, `NoMemory = 6`: "the driver cannot hold a ring of these
  counts; the connection stays, and the client may ask for fewer." It is
  checked after `Attached`.
- **The statuses' order** (l.61-67) loses "an `Attach` with no Memory, one
  given without `MemoryRight.Map`, or one shorter than the ring", and gains
  `BadOp` for any request that carries a handle.
- **Failure** (l.69-82): the first check on the client's ring is no longer
  the Memory an `Attach` gives; a client's values that fail a check (indexes,
  lengths) still end the connection and count in `ring_faults`. The client's
  own checks of the ring it is given end its use of the connection.
- **`attach_reply`** (l.553 on) reads the reply's handle as well, and the
  typed client API returns it beside `AttachInfo`.
- **`rx_dropped`'s doc** gains §5.2's sentence.

`sockproto` and `hciproto` keep the client-allocates shape for now; whether
they follow is their units' question.

---

## 6. The runner's peer

### 6.1 The backend and its syntax (verified)

QEMU 11.1.3 accepts both of these (exp3):

```
-netdev stream,id=net0,server=off,addr.type=inet,addr.host=127.0.0.1,addr.port=PORT
-netdev stream,id=net0,server=off,addr.type=fd,addr.str=FD
```

A frame crosses as a 4-byte big-endian length, then the frame:
`net/stream_data.c` `net_stream_data_receive` l.34 `uint32_t len =
htonl(size)` and l.38; frames read back through `net_fill_rstate` (l.127).
Exp3 sent one 60-byte frame that way and QEMU's `filter-dump` recorded it
whole (`caplen 60 origlen 60`, the bytes as sent).

**The start race.** A stream client connects asynchronously:
`net/stream.c` calls `qio_channel_socket_connect_async` (for an `fd` address
as well), sets `link_down = true` while connecting, and `stream_data.c`
clears it only in the connect callback (l.180). `info network` showed
`connecting` at first for both an `inet` and an `fd` address (exp3). QEMU
delivers nothing through a netdev whose `link_down` is set, so a guest that
transmits before the callback loses the frame, and how soon the callback runs
depends on the host. A case must not depend on that.

**The fix: start paused, resume on the event.** QEMU started with `-S` and a
QMP monitor on an inherited socket emits `NETDEV_STREAM_CONNECTED` when the
stream connects, while the guest is still in `prelaunch`; `cont` then starts
it (exp4, exp4b):

```
-S
-chardev socket,id=qmp0,fd=FD
-object monitor-qmp,id=qmp,chardev=qmp0
```

(`-mon chardev=...,mode=control` works but QEMU 11 warns it is deprecated;
the `-object` form gives no warning, exp4b.) An event raised before
`qmp_capabilities` is not delivered, so the runner, after negotiating, asks
`human-monitor-command` `info network`, and waits for the event only if the
netdev still reads `connecting`. Either way the guest's first instruction
runs with the stream connected.

### 6.2 Deterministic and parallel-safe

- One peer process per case, listening on `127.0.0.1` port 0 (the system's
  choice, `TcpListener.listen(port: 0)` and `local_port()` in Saw's
  `std/net.saw` l.599-631), so no two cases share a port and no fixed port
  can collide.
- One QMP socketpair per case, passed by descriptor: no file system path
  (a Unix socket path in a staging directory would also run into macOS's
  104-byte limit).
- Loopback only: no frame leaves the host, and no case uses `-netdev user`.
- The guest starts only with the stream connected (§6.1).
- The peer only answers: every frame it sends is caused by a guest frame,
  including a burst (§6.4). Nothing on either side waits on a clock.
- The guest stack keeps no clock (§7.1), so no ARP entry ages and no request
  repeats.

### 6.3 The runner's net path

A case with a `net` key runs through a new `_run_net_qemu` beside
`_run_qemu` (`tools/sos_runner.py` l.15890-15960):

1. Start `netpeer serve --script NAME`; read its first stdout line,
   `port N`, within `QEMU_TIMEOUT_S` (l.1026).
2. Make the QMP socketpair. Build the command: the arch's `qemu_args`,
   `-nographic -kernel ELF -S`, the QMP pair, then
   `-global virtio-mmio.force-legacy=false` (unless the case is the legacy
   refusal), the `-netdev stream` above, and
   `-device virtio-net-device,netdev=net0,bus=virtio-mmio-bus.N,mac=52:54:00:12:34:56`
   plus the case's `props` (`status=off`, `host_mtu=1400`). `N` is the
   arch's `net_bus` (7 on riscv32 and the flat profile, 24 on arm64), a new
   key in `ARCHES` next to the board rows it matches (§2.3). The case's
   `device` replaces `virtio-net-device` for the wrong-device refusal
   (`virtio-rng-device`), and `none` leaves it out.
3. QMP: greeting, `qmp_capabilities`, the connected check of §6.1, any
   `before_cont` commands the case gives (`set_link net0 off`), `cont`.
4. Serial as `_run_qemu` does it (an empty pipe held open), stdout captured,
   the same timeout. A case's `on_console` pairs (a console line, a QMP
   command) send the command when the line appears, for the link case
   (§7.3); the runner reads stdout as it arrives for these cases.
5. After QEMU exits, read the peer's stdout to its end and wait for it (it
   exits when QEMU's end of the stream closes), bounded by the same timeout.
   A peer that exits non-zero fails the case with its stderr.
6. Match `expect_out` against the console as today (entries in order, each a
   substring found after the previous one, `tools/sos_runner.py`
   l.16030-16037), and `expect_peer`, the peer's lines in order, exactly.

`files(name)` (l.2541-2544) gives the three image descriptions, so a net
case is an `image_file` case like `console_service`.

### 6.4 What the peer answers

The peer is at 10.0.2.2 with MAC `52:55:0a:00:02:02`; the guest is 10.0.2.15
on 10.0.2.0/24 with gateway 10.0.2.2 (QEMU's user-network numbers, familiar
to a reader of a transcript; nothing reaches QEMU's own user network).

Four scripts. `ping` is the base; `burst` and `log` add to it; `silent`
answers nothing.

| Guest frame | `ping` | `burst` | `log` | `silent` | Log line |
|---|---|---|---|---|---|
| any frame, before its own row | | | logged | logged | `peer: frame N bytes` |
| ARP request, sender = target = guest (gratuitous) | logged | logged | logged | | `peer: arp announce 10.0.2.15 at 52:54:00:12:34:56` |
| ARP request for 10.0.2.2 | ARP reply | ARP reply | ARP reply | | `peer: arp request for 10.0.2.2 from 10.0.2.15 at 52:54:00:12:34:56, answered` |
| ICMP echo request to 10.0.2.2, checksums good | echo reply, same id, sequence and data | the same | the same | | `peer: echo request 1 from 10.0.2.15, 56 bytes, answered` |
| UDP to 10.0.2.2:7 carrying `burst N` | | `N` UDP datagrams to the sender's port, payload `frame K of N` | | | `peer: burst of N to 10.0.2.15:PORT` |
| anything else, or a bad checksum, length or address | logged | logged | logged | | `peer: unexpected: ...` naming it |

An empty cell answers and logs nothing for that row. At its end every script
logs `peer: closed after N frames`. The peer encodes and checks frames with
its own code, not `netstack`'s, so a symmetric encoding bug in the stack
cannot pass against itself (A2's pair tests already run the stack against
itself, `drivers/netstack/tests/pair.saw`).

**Left for A4:** a fixed DHCP lease (DISCOVER, OFFER, REQUEST, ACK), one DNS
name, UDP echo on a port. The table grows rows; the process model does not
change.

### 6.5 Saw or Python

**Saw, as `tools/netpeer/`**, a hosted Blade package (the owner's ruling,
Nov 13, that new tools are Saw where possible). What the runner's process
model needs, and Saw has:

- a listener on loopback port 0 and its port: `TcpListener.listen(port: 0)`,
  `local_port()` (`std/net.saw` l.599-631);
- a blocking read and write of one stream: `TcpStream.read` (l.855) and
  `write` (l.952, l.977);
- a first line on stdout the runner reads before the peer accepts: `print`
  writes each line through `__saw_rt_write_line` (sawlang `runtime/ABI.md`
  l.1479), unbuffered;
- an exit at end of stream, with a status.

The peer needs no concurrency: it reads a frame, answers, logs, and reads the
next. The runner builds it as it builds the image builder
(`_build_sosimage`, l.15457-15468, `blade build --force`), and its hosted
tests (frame encoding and checks, each script's table rows) join
`HOSTED_TESTS` (l.15400-15411). A Saw gap found on the way is reproduced and
filed in SL for the sawlang lead, not worked around in Python.

A Python thread inside the runner would be shorter and could take a
socketpair by descriptor, but it would be the opposite of the ruling for no
need the Saw program cannot meet.

### 6.6 No time

No case asserts time (rule 7.1, networking.md l.1251-1254; CLAUDE.md). Here:
the guest starts after the connection; the peer answers only guest frames;
the guest stack is given `now = 0` always (§7.1), so nothing ages or
repeats; the images have `tick = false`. The one clock left is the harness
timeout, which is its job. Every count the cases print is then fixed by
construction, and asserted.

---

## 7. `net_ping` and its siblings

### 7.1 The image

`tests/images/net_ping.toml` (riscv32), `net_ping_flat.toml` (board
`riscv32-virt-flat`, the same rows), `net_ping_arm64.toml` (`VIRTIO24`):

```toml
format = 1

[image]
name = "net_ping"
board = "riscv32-virt"
tick = false

[root]
package = "stock"

# The driver's queues (one page) and one client's ring (8 + 8 slots,
# 32,896 bytes, a page-rounded 0x9000 on arm64).
[[pool]]
name = "netq"
len = 0xA000

[[process]]
name = "net"
package = "sawos:drivers/svc-virtio-net"
device = { net = "VIRTIO7" }
pools = { queues = "netq" }
ceilings = { memory = ["transfer", "mint", "map", "physical"], pool = ["allocate", "map", "physical"] }
on-exit = "shutdown"

[[process]]
name = "ping"
package = "sawos:tests/net-ping"
clients = { netdev = "net.netdev" }
```

`tests/net-ping` is a test package written with `main`, depending on `sos`,
`sosmain`, `netdevproto`, `pktring` and `netstack` (`svc-net` is A6). Its
declaration: `system` and a `client` of `netdevproto`; no `memory` record,
since the ring now comes from the driver; quota `mappings = 1`. It asks for
no `physical`, and gets none by the default (§3.5). Its ring is 8 slots each
way: `Layout.bytes()` is 128 + 16 × 2048 = 32,896 bytes.

What it does:

1. `Attach(8, 8)`; check the handle in the reply (`Map` held, the mapping no
   shorter than the layout), map it, `Endpoint.client_given`; print the
   reply;
2. `WaitEvent`; expect the link's state; print it;
3. `Stack.init(mac)`, `configure` 10.0.2.15/24 via 10.0.2.2 (which sends the
   gratuitous ARP);
4. three times: `ping` 10.0.2.2 with 56 bytes of a fixed pattern, ident 1,
   sequence k; `WaitEvent` until the stack delivers `EchoReply` for k; print
   it;
5. `Stats`, print the driver's counts and the stack's drops; exit 0.

Its `Sink` writes frames with `Endpoint.send`, publishes, and tells `Tx`;
`RxReady` frames are read with `receive` and handed to `Stack.receive`,
then released and returned with `RxReturn`. **It passes `now = 0` to every
stack call and never calls `poll`.** The stack has no clock of its own
(`netstack` lib.saw l.20-27), an ARP reply flushes the waiting frame with no
reference to time (l.459-470, `flush` l.690-698), and only `poll` drops a
waiting frame (l.324-334). So nothing in the pinger depends on host time.

### 7.2 The expected transcript

Console, as `expect_out` entries (the same on all three profiles). The runner
finds each entry as a substring after the previous one
(`tools/sos_runner.py` l.16030-16037), so each line is its own entry, in
order, with the kernel's exit line for each child that exits
(`kernel/core/sched.saw` l.666-685: `SOS: process exit: code=... process=...`
for every child whose own exit ends it). The driver is process 1 and the
pinger process 2, in image order with root 0 (`kernel/core/limits.saw` l.98),
as in `console_print`, whose second child's line is `process={two}`
(`tools/sos_runner.py` l.2291-2300).

```python
"expect_out": [
    "{banner}",
    "SOS netdrv: virtio-mmio version 2, network device, features version_1 mac status",
    "SOS netdrv: mac 52:54:00:12:34:56, link up, mtu 1500, queues 64 and 64",
    "SOS netping: attached, mac 52:54:00:12:34:56, mtu 1500, slots 8 and 8",
    "SOS netping: link up",
    "SOS netping: 10.0.2.15 pinging 10.0.2.2",
    "SOS netping: reply 1 from 10.0.2.2, 56 bytes",
    "SOS netping: reply 2 from 10.0.2.2, 56 bytes",
    "SOS netping: reply 3 from 10.0.2.2, 56 bytes",
    "SOS netping: driver rx=4 rx_dropped=0 tx=5 tx_errors=0 ring_faults=0",
    "SOS netping: stack drops=0",
    "SOS: process exit: code={zero} process={two}",
    "root: ping ended status=65536",
    "SOS netdrv: client gone: device reset, ring unmapped",
    "SOS netdrv: rx=4 tx=5 tx_errors=0 ring_faults=0",
    "SOS: process exit: code={zero} process={one}",
    "root: net ended status=65536",
],
"expect_clean_exit": True,
```

Root stops the machine with the driver's exit code 0 (`root/src/main.saw`
l.43-45). TX is the gratuitous ARP, the ARP request and three echo requests;
RX the ARP reply and three echo replies. The implementer confirms the order
of the two driver start lines against the pinger's on the first run: with no
tick, root starts the driver first and the driver runs until it waits.

Peer (`expect_peer`, script `ping`):

```
peer: arp announce 10.0.2.15 at 52:54:00:12:34:56
peer: arp request for 10.0.2.2 from 10.0.2.15 at 52:54:00:12:34:56, answered
peer: echo request 1 from 10.0.2.15, 56 bytes, answered
peer: echo request 2 from 10.0.2.15, 56 bytes, answered
peer: echo request 3 from 10.0.2.15, 56 bytes, answered
peer: closed after 5 frames
```

### 7.3 The cases

Every check the driver makes has a case that goes red when the check is
removed or inverted: a gate case where QEMU or a client can produce the
value, a hosted test (§7.4) where only a misbehaving device could. The
kernel's and the builder's new checks have their own pins (§3.4, §7.5).

Gate cases (all on riscv32, riscv32-flat and arm64; none asserts a hardware
denial, so the flat profile excludes none). Exit codes are §4.3's.

| Case | Image / QEMU | Check it holds | Expected |
|---|---|---|---|
| `net_ping` | §7.1 | the whole path | §7.2 |
| `net_no_status` | `props = "status=off"` | `STATUS` optional (§1.2) | `features version_1 mac`, `link assumed up`, the three replies |
| `net_mtu` | `props = "host_mtu=1400"` | `MTU` taken when offered, reported | `features version_1 mac status mtu`, `mtu 1400`; the pinger's `attached ... mtu 1400` |
| `net_legacy` | no `force-legacy=false` | `Version` 2 | `SOS netdrv: refused: virtio-mmio version 1, not 2`; exit 1, machine status 1 |
| `net_wrong_device` | `device = "virtio-rng-device"` | `DeviceID` 1 | `refused: device 4, not a network device`; exit 1 |
| `net_no_device` | `device = "none"` | `DeviceID` 0 | `no device at this transport`; exit 1 (the deviation of §2.4, Q10) |
| `net_link_down_first` | `before_cont` `set_link net0 off`; `on_console` `SOS netclient: waiting for link` → `set_link net0 on` | link state from `STATUS`, the configuration-change interrupt | first event `LinkDown`, then `LinkUp`, then a ping answered |
| `net_tx_length` | script `log`; client sends a 2000-byte frame (within the slot, past 1514), then a 10-byte one, then pings | frame length check | `tx_errors=2`; the peer's log has the ping's frames, each after its `peer: frame N bytes` line, and no `frame 2000 bytes` or `frame 10 bytes` |
| `net_ring_index_past` | client stores ToServer head = 2n + 3, rings `Tx`, then calls `Stats` | ToServer head below 2n | driver: `connection ended: an index past the slot count: 19, ring_faults=1`, exit 2; client's call answers `PeerClosed` |
| `net_ring_index_order` | head moved back | in order | `an index out of order` |
| `net_ring_length_past` | a length word of 4096 | length against slot | `a length past the slot: 4096` |
| `net_ring_tail_past` | ToClient tail = 2n + 1, rings `RxReturn` | ToClient tail checked | `an index past the slot count` |
| `net_attach_too_large` | `Attach(256, 256)` (about 1 MiB) against `netq`'s 0xA000, then `Attach(8, 8)` | the pool's `NoMemory` answered as the status, the connection kept | `NoMemory`, then an attach that succeeds and a ping answered |
| `net_attach_handle` | an `Attach` that carries a handle (an inlet sibling of its own) | a request carrying a handle | `BadOp`, then a plain `Attach` that succeeds |
| `net_ring_no_physical` | client calls `Physical` on the ring handle the driver gave it | the client's handle is minted without `Physical` (§5.2) | the client faults `AccessDenied` (the kernel's fault line names it), and the driver then reports `client gone` |
| `net_protocol_states` | `Tx` before `Attach`; `Attach` twice; `WaitEvent` while one is held; a `Tx` range valid for `MAX_SLOTS` but past the 8-slot ring (first 8, count 1) | statuses in order; the range against the attached ring (§4.2) | `NotAttached`, `Attached`, `Busy`, `BadArg` |
| `net_slow_client` | script `burst`: client sends `burst 20`, holds every slot until 8 have arrived, then returns slots one at a time | the driver posts only freed slots, publishes in order | the client receives `frame 1 of 20` to `frame 20 of 20` in order, intact; `rx_dropped=0` |
| `net_client_dies` | client attaches, sends an echo request, exits with its RX buffers posted | reset confirmed before unmap; the terminal level | `client gone: device reset, ring unmapped`; machine status 0 |
| `net_wait_abandoned` | client: `Attach`; `WaitEvent` (answered `LinkUp`); posts `WaitEvent` and keeps the claim; **yields** (no tick, so the yield returns only once the driver has taken the request, attached it one-shot and parked in `wait`); drops the claim, which writes the abandoned delivery into the driver's `wait` there; pings 10.0.2.2; calls `WaitEvent` | abandoned obligation discarded | the second call is answered `RxReady`, not `Busy`; the driver's last line counts `abandoned=1` |

`net_wait_abandoned` is forced as `tests/uart-cancel` is (its comment,
l.19-25: "Root yields after posting, and this image arms no scheduler tick,
so the yield returns only once the driver has taken the request and parked
in `wait` ... the release that abandons the claim wakes the driver there,
with that record, before anything else is posted"). The ping gives the
second call an event to be answered with, whether its frames arrive before
or after the call.

The client cases share one test package, `tests/net-client`, whose scenario
is a build input (`netcase`, given by each image's `inputs`, as
`tests/images/stock/alarm-whole.saw` is given today). The machine-ending
order of the hostile cases (the client's `PeerClosed` line before root stops
on the driver's end) follows from the ready list: the client is woken when
the outlet is released, before the driver's exit wakes root, and a wake
never preempts (CLAUDE.md, "A wake never preempts"). The implementer confirms
it on the first run; if it does not hold, the client's line is left out of
the expected text rather than ordered by time.

Revision 2's `net_attach_short` and `net_attach_no_map` are gone: the
client no longer gives the driver memory, so the driver has no such check.
The client's checks of the ring it is given are `pktring`'s
(`SharedWindow.init`'s `ShortMemory`, the `Map` right), pinned by
`pktring`'s hosted tests; a driver that gave a short or unmappable ring is a
driver bug no case can make QEMU produce.

### 7.4 Hosted tests (`drivers/virtio`)

The transport and queue logic is a hosted library, `drivers/virtio`, over
two traits:

- `Registers`: `read32(offset)` and `write32(offset, value)` for the control
  registers of Table 4.1, and `config_read8(offset)` and
  `config_read16(offset)` for the config space at 0x100 on, each at its
  field's width (§4.3);
- `Dma`: words of the queue memory, and the barrier.

The process supplies the volatile window (`UnsafeMemory<UInt32, Device>`,
`<UInt16, Device>`, `<UInt8, Device>`), the mapped queue memory and the
native fence; the tests supply a fake device that behaves as QEMU does, then
misbehaves one way at a time. Each row is a test that goes red when its
check is removed or inverted:

- `MagicValue` wrong, and no register touched after it (not even
  `Version`); `Version` 1 and 3, and no register written; `DeviceID` 0 and 4,
  and no register written;
- `VERSION_1` not offered; `MAC` not offered; `ACCESS_PLATFORM` offered;
  `STATUS`, `MTU` and `ORDER_PLATFORM` offered and not; a feature outside the
  accepted set never written back; each refusal sets `FAILED`, then resets;
- `FEATURES_OK` cleared by the device;
- `QueueReady` 1 before setup; `QueueNumMax` 0, 1, 32, 48 (gives 32) and 256
  (gives 64);
- `mtu` 67 when `MTU` is offered;
- `ConfigGeneration` changing once during the `mac` read (read again, then
  accepted), and changing on every read (refused after `CONFIG_READS`);
- a 32-bit read anywhere in config space, or an 8- or 16-bit access to a
  control register, fails the fake;
- `Status` reading 0 only after k < `RESET_READS` reads (the driver waits),
  and never (refused, exit 4, nothing unmapped);
- used `idx` advanced past the chains in flight; `id` odd, past the queue,
  and of a chain not in flight; RX `len` below 12 and past 12 + the slot;
  `num_buffers` 2;
- `DEVICE_NEEDS_RESET` at a configuration change (`FAILED`, then reset);
- an undefined bit (bit 2) in `InterruptStatus`: not written to
  `InterruptACK`, not acted on, and the driver does not loop on it;
- completions out of order: TX slots released, and RX slots published, only
  over the completed prefix;
- the order of a client's end: the `Status` 0 write, then the read that
  answers 0, then the unmap (the fake records calls and completes the reset
  late, so an unmap after the write but before the read shows red);
- the barrier between descriptor writes and `avail.idx`; the `used.flags`
  read after the barrier that follows the `avail.idx` write (a driver
  reading `flags` before that barrier shows red); no notify when
  `used.flags` is 1.

`HOSTED_TESTS` gains `drivers/virtio` and `tools/netpeer`.

### 7.5 The builder's pins (B1)

Each is a builder test in `tools/sosimage/tests` (beside `process_ceilings.saw`,
`ceiling_plan.saw` and `image_file.saw`), and the first four are also gate
cases built from a description, with `expect_build_error` (the runner's key
for a case whose build must fail, `tools/sos_runner.py` l.1473-1480, used at
l.10153, l.10168, l.10324), so a refusal is seen end to end:

| Pin | Image and declaration | Expected refusal |
|---|---|---|
| `physical_not_granted` | the declaration's `[ceilings] memory` names `physical`; the image's entry has no `ceilings`, or one without `physical` for `memory` | "the process P asks for `physical` on `memory` in its declaration, and the image does not grant it: add it to the process's `ceilings` in the image" |
| `physical_not_asked` | the image's `ceilings` grants `physical` on `memory`; the declaration does not ask (no `[ceilings]`, or a `memory` row without it) | "the image grants `physical` on `memory` to the process P, whose declaration does not ask for it" |
| `ceiling_strips_kept` | the image's `ceilings` lists `pool = ["allocate"]` for a process whose `memory` record keeps `allocate` and `map` | "the image's `pool` ceiling for the process P removes `map`, which its record `queues` keeps" |
| `ceiling_strips_runtime` | the image's `ceilings` lists `process = ["exit"]` for a program written with `main` | the existing runtime refusal, naming `boot-handles` and `thread-self`, now applied to the effective ceiling |
| `ceiling_unknown` | an unknown kind, an unknown right, a right twice, a `system` key in the image's `ceilings` | each by name, as for a declaration |
| `ceiling_intersection` | the declaration lists `memory = ["map", "mint"]`, the image `memory = ["map", "transfer"]` | the plan's `memory` ceiling is `map` alone; a kind the image omits keeps the declaration's word |
| `physical_default` | no `[ceilings]`, no record `rights` | the plan's `memory` and `pool` ceilings are every right but `physical`, and the `memory` record's keep word lacks it (K1 lands this one; B1 keeps it) |

And one gate case that succeeds, `stock_physical_granted`: a test child whose
declaration asks for `physical` on `memory` and `pool`, granted by the image,
allocates contiguous from its pool, reads `Physical` and prints its length,
and allocates without the flag and prints `NoResource`. It runs with no
network device.

---

## 8. Size and units

Nine units, one optional. Sizes as §10 of the networking design uses them
(S days, M a week or two).

| Unit | Size | Track | What | Files |
|---|---|---|---|---|
| **K1** | M | kernel (networking depends on it) | `MemoryOp.Physical`, `MemoryRight.Physical` and `PoolRight.Physical`, the default sets, `AllocateFlag.Contiguous` and `MemorySlot.contiguous` (§3.4); the builder's minimum for "off by default" (`physical` named, stock children without `[ceilings]` and records without `rights` kept without it, a declaration naming it refused until B1); the consumer sweep; §3.4's cases | `spec.md` §2.5; `kernel/abi/src/ops.saw`, `rights.saw`; `kernel/core/dispatch.saw`, `objects.saw` (and the boot path that mints boot allocations); `kernel/sysapi/src/memory.saw`, `pool.saw`; `tools/sosimage/src/rights.saw`, `declaration.saw`, `plan.saw` and their tests; `tests/memory-physical*/`, `tests/pool-allocate-flags/`, `tests/stock-physical-default/`; `tools/sos_runner.py` (cases and the swept expectations) |
| **B1** | M | general builder, under the limits issue | the image's per-process `ceilings` table, the intersection, the opt-in at both ends, the refusals, the plan's keep words from the effective ceiling (§3.5, §3.6); §7.5's pins | `tools/sosimage/src/imagefile.saw`, `declaration.saw`, `plan.saw`, `rights.saw`, their tests; `spec.md` §12; `tests/stock-physical-granted/`; `tests/images/` (the pins' descriptions); `tools/sos_runner.py` |
| **K2** | S | kernel (networking) | arm64 second device window (§3.7), with the arm64 rows and the arm64 `virtio_probe` case | `hal/arm64/kernel/lib.saw`, `hal/arm64/kernel/ABI.md`, `hal/arm64/user/ABI.md`; `hal/arm64/board.toml`; `tests/images/virtio_probe_arm64.toml`; `tools/sos_runner.py` |
| **A3-K3** | M | kernel, optional | critical processes, if Q5 chooses (b) (§4.5) | `kernel/abi`, `kernel/core/sched.saw`, `dispatch.saw`, `process.saw`, `kernel/sysapi`, `tools/sosimage/src/imagefile.saw`, `plan.saw`, `root/`, `spec.md`, `tools/sos_runner.py` |
| **A3-1** | S | networking | `pktring`'s `server_laid_out` and `client_given` and the in-place accessors with their cursor rules (§4.2); `netdevproto` reversed: the `Attach` wire, `NoMemory`, the handle in the reply, `BadOp` for a request carrying a handle, the doc text (§5.5) | `drivers/pktring/src/lib.saw`, `drivers/pktring/tests/`, `drivers/netdevproto/src/lib.saw`, `drivers/netdevproto/tests/` |
| **A3-2** | M | networking | `drivers/virtio`, hosted: MMIO transport v2, negotiation, split queues with the device checks and the failure classes, fake-device tests (§7.4) | `drivers/virtio/` (new); `tools/sos_runner.py` `HOSTED_TESTS` |
| **A3-3** | M | networking | the riscv32 and flat board rows (§2.3); `tools/netpeer` (§6.4-6.5) with hosted tests; the runner's net path (§6.3); `tests/virtio-probe` and the cases `virtio_probe` and `virtio_probe_empty` on riscv32 and the flat profile | `hal/riscv32/board.toml`, `hal/riscv32-flat/board.toml`; `tools/netpeer/` (new); `tools/sos_runner.py`; `tests/virtio-probe/`; `tests/images/virtio_probe.toml`, `virtio_probe_flat.toml` |
| **A3-4** | M | networking | `drivers/svc-virtio-net` (its declaration with `[ceilings]`, §2.5); `tests/net-ping`; `net_ping`, `net_no_status`, `net_mtu`, `net_legacy`, `net_wrong_device`, `net_no_device`; the spec's DMA sentence and the comment on the one-binding refusal (§10, §4.5) | `drivers/svc-virtio-net/` (new, with `native/fence-riscv32.c`, `native/fence-arm64.c`); `tests/net-ping/`; `tests/images/net_*.toml`; `tools/sosimage/src/imagefile.saw` (a comment); `spec.md`; `tools/sos_runner.py` |
| **A3-5** | S-M | networking | `tests/net-client` and the rest of §7.3's table | `tests/net-client/`; `tests/images/net_*.toml`; `tools/sos_runner.py` |

K1 grows from S to M: it now carries a new op with a record, two rights, a
flag in an existing word, a slot field, two default sets and the builder's
default, against S for the Mapping op it replaces. A3-K1b is gone.

**Files shared.** `tools/sos_runner.py` is touched by every unit but A3-1,
so by the file-overlap rule no two of those run at once. `spec.md` is touched
by K1, B1, A3-K3 and A3-4; `tools/sosimage` by K1, B1, A3-K3 and A3-4 (a
comment); `hal/arm64/board.toml` only by K2. A3-1 touches none of them and
pairs with any unit.

**Order** (at most two at once):

1. K1, with A3-1 beside it. K1 first because B1 and A3-4 both need its
   right, and the builder's table cannot name `physical` before the kernel's
   enum has it (`rights.saw`'s tests hold the table to the enums).
2. A3-3 (riscv32 and flat).
3. B1, after K1.
4. A3-2.
5. K2, after A3-3: it adds the arm64 rows and the arm64 probe case to A3-3's
   package and runner path.
6. A3-K3, here, if Q5 chooses (b).
7. A3-4, after A3-1, A3-2, A3-3, K1, B1 and K2 (and A3-K3 if chosen).
8. A3-5, after A3-4.

---

## 9. Questions for the owner

Q1 is ruled (`rulings-nov25.md`): `MemoryOp.Physical`, off unless the
declaration asks and the image grants, the default changes, pools carrying
the right, the `contiguous` flag, and the driver allocating the ring. The
others stand, updated for the rulings.

2. **Give arm64 processes a second device window, the 2 MiB block at
   0x0A00_0000, at 4 KiB a table set (64 KiB of `.bss`) (§3.7)?**
   Recommended: yes; no arm64 virtio driver can map its transport without it.
3. **Modern virtio-mmio only: the cases pass
   `-global virtio-mmio.force-legacy=false`, and the driver refuses a version
   1 transport (§1.1)?** Recommended: yes.
4. **Zero copy: descriptors point at the ring's slots, through the new
   in-place accessors (§4.2), rather than the driver copying each frame?**
   Recommended: yes. The ring is now the driver's own allocation, and the
   driver acts only on index and length words, read once and checked; the
   device touches the bytes.
5. **The driver's death (§4.5).** Every byte the device can address is now
   in the driver's one pool, which the image binds to the driver alone and
   whose record the driver keeps without `Transfer` or `Mint`; a boot pool
   with no holder returns its bytes nowhere; a ring a live client maps stays
   out of the pool. So in a stock image no byte the dead driver's device
   still addresses is handed to anyone, and the death needs no halt ahead of
   the frees. Options: (a) accept the argument for stock images, record it in
   the spec (§10), keep `on-exit = "shutdown"` as the image's policy, and
   schedule critical processes when driver restart or sub-pools are; (b)
   build critical processes (A3-K3, M) first and mark the driver critical.
   Recommended: (a). Trade-off: (a) adds no unit and now rests on three
   things the tree enforces (the builder's one-binding rule, the record's
   keep word, the boot-pool rule), but a hand-written root can still give the
   pool away; (b) is general and puts an M kernel and builder unit in front
   of A3-4.
6. **A ring fault releases the driver's one outlet, which ends the
   connection for every client, and the driver then exits 2, ending the
   image (§5.4)?** Recommended: yes for A3, whose one client is the stack: a
   stack that corrupts its own ring is a stack bug, and netdevproto already
   says the connection ends. Revisit when A6 puts several clients behind
   `svc-net` (they would be `svc-net`'s clients, not the driver's).
7. **Board rows: each transport on riscv32; on arm64, one row per page of
   eight transports, named for its first transport, with devices placed only
   there (§2.2-2.3)?** Recommended: yes. A page is arm64's smallest window, so
   a row that pretends to be one 0x200 transport would be refused by the
   builder and could not be mapped.
8. **The peer in Saw (`tools/netpeer`), one process per case on loopback
   port 0, QEMU started paused and resumed on QMP's
   `NETDEV_STREAM_CONNECTED` (§6)?** Recommended: yes. The paused start is
   what makes the first frame's delivery independent of host timing.
9. **`STATUS` optional, with the link reported up without it, as the
   virtio spec says (§1.2)?** Recommended: yes. Requiring it would refuse a
   device the spec says a driver can run.
10. **Report an empty transport (`DeviceID` 0) with a line and exit 1,
    against §4.2.2.2's "MUST NOT report any error" (§2.4)?** Recommended:
    yes. The rule protects a driver that probes from noise about empty
    slots; this driver was bound to the transport by its image, and staying
    silent would hide an image or emulator error. The alternative is to exit
    1 with no line, leaving root's `ended status=...` line as the only sign.
11. **The SHOULD-accept bits this driver declines: `RING_PACKED`,
    `NOTIF_CONFIG_DATA` and `STANDBY`, and `ACCESS_PLATFORM` refused rather
    than accepted (§1.2)?** Recommended: yes; each reason is in §1.2's
    table, and QEMU offers none of them by default.
12. **(New) `physical` is an opt-in on both kinds that carry it, `memory`
    and `pool`, each needing the declaration's ask and the image's grant
    (§3.5)?** The ruling names `memory`; a pool carries the bit for its
    allocations, so a driver needs it on its pool record too. Recommended:
    yes, per kind, so the refusals name the kind exactly. The alternative, a
    `memory` grant implying the pool record's bit, saves one entry in the
    image and makes the pool's keep word depend on another kind's table.
13. **(New) `Physical` on an object not created contiguous answers the
    `NoResource` status (§3.4)?** Recommended: yes. It is what `map` answers
    for memory the kernel cannot serve as asked, and the holder of a given
    object cannot check how it was made. The alternative is a new status
    (`NotContiguous`); statuses are numbered by the vDSO discipline, so one
    can be added, at the cost of one more word every caller's match must
    know.

---

## 10. Spec changes proposed

| Where | Change | Unit | Question |
|---|---|---|---|
| spec.md §2.5, "A region is a capability, not a description" (l.1020-1031) | The first sentence, "There is no op on a pool or an allocation that reads its bounds", becomes: "One op on an allocation reads its bounds, `MemoryOp.Physical`, for a driver that hands the bytes to a device: it needs a right no process holds unless its launcher grants it, and it answers only for an allocation made contiguous. No op on a pool reads its bounds." The rest of the paragraph stands. | K1 | ruled |
| spec.md §2.5, `Pool` (l.1040-1236) | `allocate(len, granularity, from)`'s third word is `from \| flags`: `AllocateFlag.Contiguous` (bit 8) beside `AllocateFrom`, any other bit the `BadArg` fault; an allocation made with it is one physical range now and in any future kernel. "The rights, bit by bit" (l.1209-1220) adds `.Physical` (bit 12) among the bits carried for allocations, and `pool_rights()` holds it. "An allocation's rights come from the pool handle" (l.1192-1208) names `Physical` among a `Memory`'s bits, held by `memory_rights()` and not by `front_allocation_rights()`. | K1 | ruled |
| spec.md §2.5, `Memory` (l.1238 on) | "three ops, `Map`, `Extend` and `Physical`"; the rights paragraph (l.1250 on) adds `.Physical` (bit 12), minted by `memory_rights()` at boot because attenuation is monotonic; a new bullet for `Physical`: the record, its answers in order (§3.4), the contiguous rule, and that a boot allocation is contiguous by construction; `extend(len)` (l.1387 on): a contiguous allocation grows in place or answers `NoMemory`, now and in any future kernel | K1 | ruled |
| spec.md §2.5, DMA paragraph (l.1558-1574) | a DMA driver learns bus addresses from `MemoryOp.Physical` on allocations it made contiguous. The last sentence, "no case in the suite is a DMA driver and no capability in the tree is a lie about one", becomes: "`svc-virtio-net` on the QEMU `virt` boards is the suite's DMA driver, and a TCB member. Every byte its device is given lies in its own pool, which a stock image binds to it alone and gives it without `Transfer` or `Mint`, and a boot pool whose holders are gone returns its bytes nowhere, so those bytes are never handed out again after the driver dies, and its death needs no critical-process halt. A launcher that shares or keeps a DMA driver's pool, or restarts the driver, needs one." | A3-4 | Q5 |
| spec.md §2.7, rights ceilings (l.1869-1940) | a sentence: a launcher that follows §12's convention leaves `MemoryRight.Physical` and `PoolRight.Physical` out of a child's ceilings and records unless the child's declaration asks and its image grants; root's ceilings stay unrestricted | B1 | ruled |
| spec.md §12, "The stock root's plan" (l.4925-4950) | "the rights word each of its ceilings is narrowed to" no longer only "when its declaration has `[ceilings]`": every child's plan narrows `memory` and `pool` at least, the words being the declaration's ceilings intersected with the image's | B1 (K1 for the default) | ruled |
| spec.md §12, "The launch convention" (l.5096-5129) | the launcher narrows each child's ceilings to its declaration's intersected with its image's; a record with no `rights` keeps every right of its source except `physical` | B1 | ruled |
| spec.md §12, "Ceilings and record rights" (l.5130-5160) | "A declaration with none leaves the child the launcher's ceilings" becomes "every right except an opt-in one (`physical`)"; a record with none, likewise; a new paragraph for the image's per-process `ceilings` table (option B: the image's ceiling for each kind it lists, intersected; a kind it omits left as the declaration says); opt-in rights, which must appear in both; the refusals of §3.5; and the last sentence, "An image cannot widen or narrow a declaration's ceilings", becomes "An image narrows a declaration's ceilings with its `ceilings` table and never widens them, and grants an opt-in right only to a process whose declaration asks for it" | B1 | ruled |
| `hal/arm64/kernel/ABI.md`, `hal/arm64/user/ABI.md` | two device blocks, not one | K2 | Q2 |
| `drivers/netdevproto` docs (not the spec) | `Attach` reversed, `NoMemory`, `BadOp` for a request with a handle, `rx_dropped` on a zero-copy driver, the first `WaitEvent` (§5.5) | A3-1 | ruled; Q6 |
| spec.md §2.7, §8, DMA paragraph | with Q5 (b): critical processes | A3-K3 | Q5 |

---

## 11. What was checked, how

| Fact | How |
|---|---|
| current main | `git clone --depth 1 https://github.com/swoodtke/sawos.git` into `so103a3/main`: `850c5849`, the local checkout's commit |
| `MemoryOp`, `PoolOp`, `AllocateFrom`, `MemoryRight`, `PoolRight` numbers and default sets on main | `kernel/abi/src/ops.saw` l.741-744, l.768-792; `kernel/abi/src/rights.saw` l.630-700, l.1218-1250 |
| `Allocate`'s decode and mint, `Extend`, `MemorySlot` | `kernel/core/dispatch.saw` l.3403-3420, l.3523-3560; `kernel/core/objects.saw` l.1116-1175 |
| a reply carrying handles | `kernel/sysapi/src/pipe.saw` l.1440-1458 (`reply_with`) |
| ceilings, gives and donations in the spec | spec.md l.1869-1940, l.2282-2300, l.4925-4950, l.5096-5160 |
| the builder's ceilings, keep words, plan rows, the one-binding rule, `expect_build_error` | `tools/sosimage/src/declaration.saw` l.494-525, l.1019; `plan.saw` l.496-500, l.556-580; `imagefile.saw` l.462, l.483, l.604; `rights.saw` l.1-40; `tools/sos_runner.py` l.1473-1480 |
| the runner's rights-word expectations the default sets change | `tools/sos_runner.py` l.5213-5911 (`1539`, `69123`) |
| transports, addresses, lines, `dma-coherent` (every arm64 transport node, no riscv32 one), edge flag on arm64 | `-M virt,dumpdtb=...` with a `virtio-net-device` on both machines, `dtc -I dtb -O dts` (`so103a3/exp1.sh`, `rv.dts`, `a64.dts`) |
| default bus placement; `force-legacy = true`; region 0x200 | HMP `info qtree` (exp1) |
| `Version` 1 by default, 2 with `force-legacy=false`; `DeviceID` 1 at the device, 0 elsewhere; `bus=virtio-mmio-bus.0` places at 0x1000_1000 | HMP `xp /4wx` on a paused machine (exp1) |
| QEMU's offered config (`mac`, `status` = 1, nothing past), `QueueNumMax` 256 | HMP `xp` at 0x10008100 and 0x10008034 with `force-legacy=false` (`so103a3/exp2.sh`) |
| `host_mtu` offering `MTU` | QEMU's source, `virtio_net_device_realize`: `if (n->net_conf.mtu) n->host_features \|= (1ULL << VIRTIO_NET_F_MTU);` and `DEFINE_PROP_UINT16("host_mtu", VirtIONet, net_conf.mtu, 0)`. `so103a3/exp5.sh` reads the config with `host_mtu=1400`; it has not run, since the suite lock was held by another session through revisions 2 and 3, and the A3-4 implementer runs it before writing `net_mtu`'s expected text |
| `virtio-net-device` properties and defaults (`status`, `mrg_rxbuf`, `host_mtu`, `iommu_platform`, `mq`, `packed`) | `-device virtio-net-device,help` |
| `-netdev stream` syntax (`inet` and `fd`), the 4-byte length framing, `connecting` at start, the NIC's link bit independent of the stream | `so103a3/exp3.py` with `filter-dump` |
| paused start, `NETDEV_STREAM_CONNECTED`, `prelaunch` then `cont`; `-object monitor-qmp` | `so103a3/exp4.py`, `exp4b.py` |
| QEMU source facts (irq level, `force-legacy` default, reset, ACK, no `VERSION_1` enforcement; RX drop of a frame that does not fit, header length, link status notify; stream `link_down` and framing; the PLIC's pending rule) | QEMU master `hw/virtio/virtio-mmio.c`, `hw/net/virtio-net.c`, `hw/intc/sifive_plic.c`, `net/stream.c`, `net/stream_data.c`, read online; line numbers approximate |
| virtio 1.2 sections and quotes | OASIS virtio-v1.2-cs01 HTML, downloaded and converted to text (`so103a3/spec/v.txt`); every section number and quote here was found in it: §1.4, §2.1, §2.2.1, §2.4.1-2.4.2, §2.7, §2.7.4, §2.7.10.1, §2.7.13, §2.7.13.4.1, §2.7.14, §3.1.1, §4.2.2-4.2.4, §5.1.1-5.1.6, §6, §6.1 |
| arm64 grant and device windows, table pool, RAM attributes | `hal/arm64/kernel/lib.saw` l.488-608, l.1085-1360, l.2060-2100 |
| the teardown's order and exit line | `kernel/core/sched.saw` l.641-700 |
| the netstack's time use | `drivers/netstack/src/lib.saw` l.20-27, l.287-334, l.421-470, l.690-698; `arp.saw` l.99-226 |

Each experiment ran under the sawos suite lock, taken with `mkdir` and given
back with `rm owner; rmdir`, only after a successful `mkdir`.
