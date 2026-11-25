# SO-103 A3: `svc-virtio-net`, the runner's network peer, and `net_ping`

Revision 2. Design for unit A3 of the networking design
(`.leadwork/networking.md`, revision 2, §10 l.1385-1390). Design only:
nothing here changes the tree. The facts it rests on were checked against the
code, the spec, the virtio 1.2 specification (OASIS cs01, read in full from a
text conversion of the published HTML, `so103a3/spec/v.txt`), QEMU 11.1.3 as
installed and QEMU's source (master: `hw/virtio/virtio-mmio.c`,
`hw/net/virtio-net.c`, `hw/intc/sifive_plic.c`, `net/stream.c`,
`net/stream_data.c`). The experiments are in the scratch directory
(`so103a3/exp1.sh` to `exp4b.py` ran; `exp5.sh` is written and not yet
run, §11); §11 lists them. The reviewer's threads are
answered one by one in `a3-replies.md`.

## Changes in revision 2

- **The driver's death (§4.5, new).** States why the bytes a dead driver's
  device still addresses cannot be handed out again in a stock image (every
  pool is bound to one process and given whole, and a boot pool or boot
  allocation with no holder returns its bytes nowhere), what that rests on,
  and where it stops holding. Critical processes (spec §2.5) are priced as an
  optional unit, A3-K3, and the choice is the owner's (Q5). The spec's "no
  case in the suite is a DMA driver" sentence is in §10.
- **Reset is confirmed before unmap.** Every teardown path is reset, `Status`
  read as 0 within a bound, then unmap (§5.4); the hosted test uses a fake
  that completes the reset late (§7.4). Reset citations are §2.4.1 and
  §2.4.2.
- **K1 (§3.4).** In a stock image only a child's own declaration can withhold
  `Physical`; the alternatives are priced (§3.6), with a declaration opt-in
  (`dma = true`) recommended and the choice left to the owner (Q1). The stats
  region's row answers the `BadArg` fault; spec l.1473 and l.1506 are added;
  `mapping_row_asked` takes the right; the consumer sweep is listed.
- **Failure paths (§4.3, new table).** An identity failure writes no register;
  a negotiation failure sets `FAILED`, then resets; a device fault in service
  sets `FAILED`, then resets. One exit-code table. Reporting an empty
  transport is named as the deviation from a MUST NOT it is (Q10).
- **Cache coherence (§4.6, new).**
- **Smaller:** config-space access widths (§4.3, §7.4); the ISR ack masked
  to bits 0 and 1 (§4.4); the `pktring` accessors' receivers, names and
  cursor rules (§4.2); unit order and file overlap (§8); a `log` peer script
  (§6.4); `net_wait_abandoned` forced by a yield (§7.3); virtio citations;
  `QueueNumMax` rounding and two bounded loops (§4.1, §4.3); the kernel's
  exit lines in the transcript (§7.2); the exact handle count, 17 (§2.5); the
  SHOULD-accept feature policy, with `MTU` and `ORDER_PLATFORM` now accepted
  and `ACCESS_PLATFORM` refused (§1.2), and a `net_mtu` case; the barrier
  before the `used.flags` read (§4.3); cross-references; doorbell ranges
  against the attached ring (§4.2); the PLIC's re-fire behaviour (§2.1).

The short version:

- Modern virtio-mmio (version 2). `VERSION_1` and `MAC` required; `STATUS`,
  `MTU` and `ORDER_PLATFORM` taken when offered; a device offering
  `ACCESS_PLATFORM` refused; nothing else. A 12-byte header in a descriptor
  of its own; frames zero-copy in the client's `pktring` slots.
- The board lists each virtio-mmio transport as a device of type
  `virtio-mmio`; the image names the one the driver takes; the driver checks
  `DeviceID` and refuses anything but a network device.
- riscv32 and the flat profile need no kernel change. arm64 needs two: a
  Mapping op that answers a row's physical address (`MappingOp.Physical`), and
  a second device window in the arm64 HAL.
- The peer is a Saw tool (`tools/netpeer`), one process per case on a
  loopback port of its own; QEMU is started paused and resumed only once QMP
  says the stream is connected.
- Eight units, one of them optional (§8).

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

### 2.5 What the declaration asks for

`drivers/svc-virtio-net/process.toml`, in the UART drivers' shape
(`drivers/svc-uart-ns16550/process.toml`):

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

[[boot]]
name = "queues"
kind = "memory"
tag = 5

[quota]
mappings = 3                # the window, the queues, the client's ring
memories = 1                # the queues' allocation
waiters = 1
handles = 17
```

(With Q1's option (ii), the declaration also says `dma = true`, §3.4.)

Every record kind exists today (`tools/sosimage/src/declaration.saw` l.1-45:
`window`, `line`, `serve`, `memory`; a `memory` record is bound to an image
pool by `pools`, `imagefile.saw` l.37-41), and the stock root gives each
(`root/bootplan/src/lib.saw` l.103-127; a `memory` record is given its pool's
row whole, `tools/sosimage/src/plan.saw` l.496-500, `Source.Row`).

**Handles, exactly, as the GPIO driver's comment counts them**
(`drivers/svc-gpio-esp32s3/process.toml`, `[quota]`): its records (five, the
outlet leaving at the attach: four), its Process, its main thread's Thread
handle, the queue Memory, three Mappings and its Waiter make eleven; a
delivery needs room for the obligation it mints and for every handle the
request carries before it mints any, so the most it holds is those eleven,
the one `WaitEvent` it holds, the request being served, and the
`PIPE_MSG_HANDLES` (4, `kernel/abi/src/ops.saw` l.1550) that request
carries: seventeen. The client's ring Memory is one of those four while the
`Attach` is served; the driver releases it once the map has answered, which
`pktring` allows (lib.saw l.87-89: "A server that releases its Memory handle
after the map loses nothing: the row holds the bytes"), so it is never held
beside them. The comment in `process.toml` says this, as the GPIO
driver's does. A row short of seventeen lets a client's request end the
driver.

`mappings = 3` fits riscv32's budget, four rows a child (`hal/riscv32/board.toml`
`[limits.default]` comment); arm64 gives 12.

### 2.6 The rules

- **One process per device:** each row is one device instance and is bound
  to one process; on arm64 the instance is the page, and the board's comment
  says the other seven transports in it stay empty.
- **Consistent names:** a range is `base` and `len` in the board, the pool's
  `len` in the image, `Mapping.len()` in the API; a slot's length in the new
  `pktring` accessors is `len` (§4.2); the case key that selects the peer's
  behaviour is `script`, as is the peer's flag (§6). The one older name kept
  is `pktring`'s `Layout.slot_bytes()`, an existing API.

---

## 3. DMA addressing on each profile

### 3.1 What the device needs

Every descriptor carries the address the device masters the bus with (virtio
1.2 §2.7.5, `le64 addr`). §6's `ACCESS_PLATFORM` entry says that with the bit
clear "the device will always use physical addresses matching addresses used
by the driver (typically meaning physical addresses used by the CPU)", and
§6.1: "If VIRTIO_F_ACCESS_PLATFORM is not offered, then a driver MUST pass
only physical addresses to the device". QEMU's `virt` machines have no IOMMU
in front of virtio-mmio, so the address is the guest physical address. The
driver needs two:

1. its own queue memory (a `Memory` from its pool);
2. the client's ring (the `Memory` the client gives at `Attach`).

Both are one allocation each, and an allocation is one physically contiguous
run of its pool (spec §2.5 l.1163-1171, "the front of the aligned run"), so a
base address and an offset are enough.

### 3.2 Per profile

| Profile | Where a RAM row lands | What `Mapping.base()` answers | Physical address known today? |
|---|---|---|---|
| riscv32 (PMP, tier 2) | at its own address (spec l.3259-3266) | the user address, which "on a tier that does not translate ... is also the physical one" (spec l.1430-1440; `kernel/abi/src/ops.saw` l.824-827) | yes: `base()` |
| riscv32-flat (tier 3) | the same | the same | yes: `base()` |
| arm64 (MMU, tier 1) | at the map cursor, inside the 4 MiB grant window from `RAM_BASE` (`hal/arm64/kernel/lib.saw` l.494-499 `map_place`, l.588 `map_target_ok`, l.2326 `USER_RAM_TOP`), backed by frames anywhere in RAM (l.527-529 `map_source_ok`) | a user address that is not the frame's | **no** |

On arm64 no op answers it. The spec says so on purpose: "There is no op on a
pool or an allocation that reads its bounds ... a `Mapping` answers where its
row is and how long it is (`Base`, `Len`) ... Where a row's address is its
memory's own, those two are the allocation's physical range" (spec
l.1020-1031). The kernel has the number: every grant row records its
physical base beside its user range (`kernel/core/pslot.saw` l.116-119,
`GrantRow.pa`; spec l.3279-3284).

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
today, whatever is decided about addresses.

### 3.4 Proposal K1: `MappingOp.Physical`

The smallest kernel change that answers the question everywhere with one
spelling.

- **Op:** `MappingOp.Physical = 4`: the physical address of the row's first
  byte, read from the grant record (`GrantRow.pa`), in the value register.
- **Right:** `MappingRight.Physical = 1 << 11`, the next free kind bit after
  `Grow` (`kernel/abi/src/rights.saw` l.750-768). `mapping_rights()`
  (l.1264-1267) mints it.
- **Which rows answer.** A row over an allocation answers its frame; a device
  window's row answers the window's own address. The stats region's row
  (spec §2.7, l.2154-2165: a row over "the kernel's own counter page", for
  which "no `Memory` handle is minted") answers the `BadArg` fault, as `Grow`
  answers it for the same row (spec l.1461): it is kernel storage no device
  is ever given, and its holder knows which Mapping it is, since
  `SystemOp.StatsRegionMap` answered it.
- **Refusals,** in `Base`'s order (`mapping_row_asked`, dispatch.saw
  l.3637-3650): no `Physical` right is the `AccessDenied` fault, a freed slot
  or an unmapped husk the `BadState` fault, a husk whose target died
  `Revoked`, then the stats row's `BadArg`. `mapping_row_asked` checks
  `MappingRight.Base` itself today; it takes the right to check as a
  parameter, and `Base` and `Len` pass `Base`.
- **API:** `sos.Mapping.physical() -> Result<UInt, SosStatus>` beside
  `base()` and `len()` (`kernel/sysapi/src/mapping.saw` l.104-130).
- **Names:** `tools/sosimage/src/rights.saw` learns `physical` among the
  mapping rights, so a declaration's `[ceilings] mapping` can name it.

**Who can withhold it, as things are.** A hand-written launcher narrows a
child's `Mapping` ceiling (spec §2.7). In a stock image it cannot be
withheld by the image's author: `[ceilings]` is a table in the child's own
`process.toml` (`declaration.saw` l.20, l.48-58), an image's `[[process]]`
has no ceilings key (`imagefile.saw` l.29-40), and a child without the table
"keeps the launcher's ceilings", which for the stock root are all ones. So
with K1 alone, every process of a stock image can learn where its own rows
lie. §3.6 prices the ways to make that conditional; Q1 is the choice.

**The recommended condition: a declaration opt-in, `dma = true`** (option
(ii) in §3.6). A package that hands memory to a bus master says so in its
`process.toml`; the builder gives every other stock-image child a `Mapping`
ceiling without `physical` (through the plan's existing per-kind ceilings,
which the stock root applies with `restrict_rights` before it gives
anything, `root/src/main.saw` l.18-20), and refuses a `[ceilings] mapping`
list that names `physical` in a declaration without `dma = true`. The image
author sees which packages say it, and `sosimage` prints a note per such
process, as it prints a part window's. The kernel stays as above.

**Spec text**, to add to §2.5's `Mapping` list after `Len` (l.1441-1452),
with "Four ops" (l.1430) becoming "Five ops":

> `Physical` (on `MappingRight.Physical`, bit 11) answers the physical
> address of the row's first byte: where the bus finds the bytes the row
> grants, which is what a device that masters the bus is given. On a tier
> that does not translate it is the number `Base` answers; on tier 1 it is
> the frame the row's first page translates to. A row covers one
> allocation, which is one physically contiguous run, so the row's bytes are
> the run from this address for `Len` bytes. A device window's row answers
> the window's own address; the stats region's row is the `BadArg` fault,
> as for `Grow`. Its other refusals are `Base`'s, in `Base`'s order, with
> `Physical`'s right in place of `Base`'s. `mapping_rights()` mints it; a
> launcher that does not want a child to learn where its memory lies narrows
> the child's `Mapping` ceiling.

and:

- l.1020-1031, after "(`Base`, `Len`, below)": "and, for a driver that hands
  the bytes to a device, where they are on the bus (`Physical`)";
- l.1473: "`mapping_rights()` mints `Unmap`, `Mint`, `Base`, `Grow` and
  `Physical`";
- l.1506: "`Unmap`, `Base`, `Len`, `Grow` and `Physical` answer `Revoked`";
- the DMA paragraph (l.1558-1574): §10.

With option (ii), §12's launch convention (spec l.4790 on) gains a sentence:
the stock root narrows `Physical` out of every child's `Mapping` ceiling but
those whose declaration says `dma = true`.

**What it exposes.** It tells the holder of a row which physical frames back
memory it can already read and write. On riscv32 and the flat profile `Base`
tells it that already. On arm64 it reveals the physical layout of the
holder's own rows and nothing about anyone else's. The number grants
nothing: using it takes a bus master, and the spec already puts any driver
of a DMA-capable device inside the TCB (l.1558-1566: "the window it was
granted is a fence around the driver, never around the device"). The
authority is the device window; the number is a fact that driver needs to
use it.

**Consumer sweep** (brief obligation 2: `map` now answers a wider rights
word). Each names `MappingRight` cases or reads a Mapping's rights, and the
implementer checks what each prints or compares: `tests/ceiling-derived`
(`NO_BASE`), `tests/mint-mapping` (`KEEP`), `tests/child-grow-leg`
(`NO_GROW`, `NO_BASE`), `tests/heapkit` (`Narrow.MappingCeiling`),
`tests/stock-ceilings-heap/process.toml` (`mapping = ["grow", "base"]`), and,
because they call `rights()` near a Mapping, `tests/stock-ceilings`,
`tests/map-needs-map`, `tests/child-ring-server`, `tests/stockkit` and
`tests/map-placed`.

### 3.5 Proposal K2: a second device window on arm64

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

### 3.6 Alternatives, priced

**Addresses:**

| Alternative | Kernel change | Cost | Verdict |
|---|---|---|---|
| **K1** `MappingOp.Physical` | one op, one right bit, the shared row check taking a right, a sysapi method, a ceiling name, spec text | none at run time | **recommended** |
| Identity-mapped RAM rows on arm64 (a `map` flag that places a row at its frame's own address, so `Base` is physical everywhere) | a new `MapAccess` bit and a second placement rule | a RAM row's user address must lie in the 4 MiB grant window at `RAM_BASE` (l.588), and frames come from all 126 MiB, so most allocations could not be placed; the grant window would have to become all of RAM's addresses, a redesign of the arm64 tables | rejected |
| The launcher tells the driver its pool's physical base (a builder-written build input) and the driver copies frames between the client's ring and its own buffers | none (K2 still needed) | the builder learns to write an input whose value it computes; the driver relies on its first allocation sitting at its pool's front (spec l.1163, which then becomes a contract an address depends on); every frame copied once more in each direction; the arm64 driver differs from the riscv32 one; nothing for a client's memory, ever | rejected |

**Who may learn it** (thread t3; Q1):

| Option | Where the condition lives | Cost | Verdict |
|---|---|---|---|
| (i) default on, the disclosure accepted | nowhere: every Mapping carries `Physical`; a hand-written launcher may narrow it, a stock image cannot | none | acceptable; the disclosure is a process's own rows' frames |
| **(ii) default off in stock images, opt-in by `dma = true` in the declaration** | the builder and the plan: every child without `dma = true` gets a `Mapping` ceiling without `physical`, applied by the stock root's existing `restrict_rights` | builder: a declaration key, a plan ceiling for those children, a refusal (`physical` in a `[ceilings]` list without `dma = true`), builder tests; root: nothing new; one `restrict_rights` op per child at boot | **recommended**: least authority in the images the tree builds, and the package that is a TCB member says so where a reader looks |
| (iii) a right on the Memory (`MemoryRight.Physical`) that `map` copies into the Mapping | the allocation's handle, so the memory's owner decides | a pool, allocation and give path that must carry the bit; and the client's ring would carry it only if the client chose to give it, so the authority sits with the memory's owner, not with the image that made the driver a DMA driver | rejected |
| (iv) a right on the target Process handle (`ProcessRight.Physical`) that `map` requires to mint `Physical` | the caller's Process handle, capped by its `Process` ceiling | a kernel condition in `map`, and the stock image would still need the builder to narrow every other child's `Process` ceiling, which is (ii)'s work plus a kernel change | rejected: (ii) without the kernel change does the same |

(ii) needs no kernel change beyond K1 and is a separate small builder unit,
A3-K1b (§8).

### 3.7 What the driver does with it

At start: map the queue allocation, read `physical()` and `base()`; every
queue address the driver writes to the device is `physical + offset`, every
CPU access `base + offset`. At `Attach`: map the client's Memory, read
`len()` (the ring check, `pktring` doc l.69-94) and `physical()`; a slot's bus
address is `physical + Layout.slot_offset(ring, slot)` (`pktring` l.257-266).
The offsets come from the driver's private layout and checked indexes (§4.2),
never from shared memory. The driver gives the device no address outside
those two allocations: no header, buffer or ring lives in its image, stack
or arena. §4.5 depends on that rule.

If the owner declines K1, riscv32 and the flat profile still work with
`base()`, which the spec defines to be physical there, and arm64's A3 rows
wait.

---

## 4. Queues and buffers

### 4.1 The queue memory

One allocation from the driver's pool (`memory` record `queues`, image pool
`netq`, `len = 0x1000`), mapped read-write into the driver and zeroed by it
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

### 4.2 Zero copy, and rule 5.2

§5.3 of the networking design says "the driver points descriptors at the
shared slots". That holds with rule 5.2 (networking.md l.912-929), because
the driver never acts on a slot's bytes: it acts on index words and length
words only, and the device, not the driver, reads or writes the bytes.

**What the driver reads from the ring, once each, and checks:**

| Field | Read | Check | On failure |
|---|---|---|---|
| ToServer head | at a `Tx` doorbell and when TX room frees | below `2n`, and in order (`pktring` `poll`, l.732-754) | ring fault: `IndexPastCount` or `IndexOutOfOrder` |
| a ToServer slot's length word | once, when the driver takes the slot | at most the slot size (`LengthPastSlot`, as `receive` l.773-776) | ring fault |
| the same length, as a frame | from the private copy | at least `ETHERNET_HEADER_BYTES` (14) and at most 14 + the reported MTU | not a ring fault: the frame is not sent, `tx_errors` counts it, and the slot completes as sent |
| ToClient tail | at an `RxReturn` doorbell and when the driver looks for free RX slots | below `2n`, and in order (`read_out_tail`, l.652-665) | ring fault |
| a doorbell's range | on the wire, then against the attached ring | `netdevproto`'s decoder refuses a range no ring can hold (`range()` l.486-491, `SlotRange.fits(MAX_SLOTS)`); the driver then checks it against the attached ring's own count (`SlotRange.fits(n)`, `pktring` l.333-336), which netdevproto's status list promises ("a range that does not fit the ring", l.61-67) | `BadArg` either way; the range is never acted on, since the index words say the same or more |

The descriptor's address is computed from the driver's private layout and
the checked slot number; its length is the checked private copy. Nothing the
client writes can move a descriptor outside its own ring, and a client that
rewrites a slot's bytes while the device reads them changes only its own
frame, which it could have sent anyway.

**What the ring cannot be trusted for, and what holds anyway:**

- The client can write into a ToClient slot the device is filling. That
  corrupts only frames sent to that client.
- The client can die with RX buffers posted in its ring. The driver's own
  row of the ring keeps the bytes out of the client's pool until the driver
  unmaps (spec §2.5 refcount invariant l.1006-1013 and l.1527-1531: "a range
  is never handed out again while a domain still grants it"), and the driver
  unmaps only after the device has confirmed a reset (§5.4). "A device MUST
  NOT send notifications or interact with the queues after indicating
  completion of the reset by reinitializing device status to 0, until the
  driver re-initializes the device" (§2.4.1), and "The driver SHOULD
  consider a driver-initiated reset complete when it reads device status as
  0" (§2.4.2). QEMU's soft reset clears each queue's `enabled`
  (`virtio-mmio.c` ~l.72-82, ~l.370-395).
- The driver can die with buffers posted: §4.5.

**New `pktring` accessors (unit A3-1).** `Endpoint` copies today (`send`
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
none panics; `take` then `receive` then `release()` releases both.

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
| the connection ended by a client fault | §4.2's ring rows, a refused `Attach` | a reset and the bounded wait, then unmap | 2 | 2 |
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
then clears its domain (spec l.1536-1545), so its queue allocation goes back
to its pool while the device may still hold the queue addresses and, in
posted RX chains, slot addresses in the client's ring. Root hears of the
death at the start of the teardown, but "the whole teardown below runs to
completion first" (`kernel/core/sched.saw` l.655-664), and only then can
root stop the machine. So `on-exit = "shutdown"` acts after the free, not
before it, as the reviewer says.

**Why that is safe in a stock image.** The device's addresses lie only in
the queue allocation and the client's ring (§3.7). Neither can be handed out
again, ever, in an image the stock root launches:

1. **Each pool has one holder.** The builder refuses a pool bound twice
   ("the pool ... is bound twice: a pool is bound once",
   `tools/sosimage/src/imagefile.saw` l.462 and l.483), and the plan gives a
   `memory` record its pool's row whole (`Source.Row`, `plan.saw`
   l.496-500), so root keeps no handle on it. `netq` is the driver's alone;
   `ring` is the client's alone.
2. **A boot pool with no holder returns its bytes nowhere.** "When its last
   handle has gone and the last allocation from it has come home, its slot
   frees ... and its bytes go nowhere: a pool minted at boot has no pool
   above it" (spec l.1221-1230). The driver's death releases its `netq`
   handle and, at the domain clear, its queue allocation's last row; the
   pool then frees and its bytes are on no list.
3. **The client's ring** is the client's own memory while the client lives
   (the device writing into it changes only frames sent to that client),
   and its pool goes the same way when the client dies.
4. **Nothing else reuses physical RAM.** Every process's region and
   destination is a boot allocation, which "came from no pool, and when it
   frees its bytes go nowhere" (spec l.1246-1249); kernel memory is its
   image and `.bss`, and slab donations come only from allocations a holder
   donates.

So between the driver's teardown and root's stop, and after it, no process
and no kernel object is given those bytes; the device's writes land in
memory nobody owns. `on-exit = "shutdown"` is then not what makes the death
safe. It stays because a dead network driver leaves nothing for the image to
do, and so that a driver death is an end the transcript reports (Q5).

**Where the argument stops holding,** and critical processes (or more) are
needed: a launcher that keeps a sibling of the driver's pool, or gives it to
two processes (a hand-written root may); a driver that is restarted into
the same pool; sub-pools and revoke (spec §2.5 l.1576 on, not built);
anything that returns a boot pool's bytes to a live pool. The builder's
one-binding rule is what makes it true in a stock image, so the argument is
written into `imagefile.saw`'s comment on that refusal and into the spec
(§10).

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
- builder and root: an image key `critical = true` (or `dma = true` in the
  declaration implying it), a plan field, root calling the op before
  `start`;
- cases on all three profiles: a critical child that exits, faults and is
  killed stops the machine before root's `ended` line, and a non-critical
  sibling does not.

About an M unit, touching `kernel/abi`, `kernel/core` (`sched.saw`,
`dispatch.saw`, `process.saw`), `kernel/sysapi`, `tools/sosimage`
(`imagefile.saw`, `plan.saw`), `root/`, `spec.md` and the runner. It is not
needed for A3's images by the argument above; it is needed by the first
image where that argument fails. Recommended: A3 goes ahead on the
argument, with critical processes scheduled when restart or sub-pools are
(Q5). If the owner prefers the general mechanism now, A3-K3 comes before
A3-4.

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
negotiate (§1.2), `FEATURES_OK` and check it, allocate and zero the queues,
set them up, read `mac`, `status` and `mtu` (§4.3's widths), set `DRIVER_OK`
(§3.1.1). The RX queue is empty until a client attaches: its buffers are the
client's slots. QEMU holds frames that arrive with no buffer posted
(`virtio_net_has_buffers` returns 0, `virtio-net.c` ~l.2010), and the stream
backend stops reading its socket, so nothing is lost before `Attach`.

Two lines, through the debug print (no console record, as the UART drivers):

```
SOS netdrv: virtio-mmio version 2, network device, features version_1 mac status
SOS netdrv: mac 52:54:00:12:34:56, link up, mtu 1500, queues 64 and 64
```

(Each negotiated optional feature is named: `status`, `mtu`,
`order_platform`. Without `STATUS`, `link up` reads `link assumed up`.)

### 5.2 The ops

| Op | What the driver does |
|---|---|
| `Attach(to_server, to_client)` + Memory | checks in `netdevproto`'s order (l.61-67). The Memory's rights hold `Map` (`rights()`), else `BadArg`, counted, connection ended. `map` it read-write; `NoResource` (spec §2.5: empty, off the grain, flash) is counted and ends the connection. `SharedWindow.init(at:len:layout:)` with `len()`; `ShortMemory` likewise. Then `physical()`, then the Memory handle is released (§2.5's count), `Endpoint.server`, post RX buffers into the free ToClient slots (as many as there are RX chains), notify, answer `AttachInfo` (mac, the reported MTU, the request's counts). The next `WaitEvent` is answered at once with the link's state. A second `Attach` is `Attached` |
| `Tx(range)` (a tell) | the range checked against the ring (§4.2); `poll` the ToServer ring; for each new slot while a TX chain is free: `take`, check the frame length (§4.2), post or complete with `tx_errors`; notify. Slots past the chains in flight wait in the ring until chains free. Answered `Ok` (and discarded, as the UART's `Write`) |
| `RxReturn(range)` (a tell) | the range checked; `reserve` every freed ToClient slot while an RX chain is free, post, notify |
| `WaitEvent` | answered at once if an event is pending; otherwise held, with a one-shot attach so an abandoned claim is a delivery (the UART's held `Read`, l.556-584). A second while one is held is `Busy` |
| `Stats` | `NetdevStats`: `rx_frames` (frames published to the client), `rx_dropped`, `tx_frames` (TX chains the device used), `tx_errors`, `ring_faults` |

**Events.** Pending state is a link flag, the ToClient range published since
the last report, and the ToServer range completed since the last report. One
`WaitEvent` answers one, in this order: a link change, `RxReady`, `TxDone`.
Frames that arrive while none is held stay published in the ring, and the
next `RxReady` names all of them (netdevproto l.47-51).

**`rx_dropped` is 0 on this device.** With zero copy the device has nowhere
to put a frame when the client holds every slot, so QEMU keeps it and stops
reading the stream: back-pressure reaches the peer instead of a drop. The
counter stays in the protocol for drivers that copy (the radio, §5.3 of the
networking design). `netdevproto`'s doc for `rx_dropped` gains that
sentence.

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
  the queues before the row that keeps the ring's bytes out of the client's
  pool goes (§4.2). A reset that does not complete is exit 4 with the ring
  still mapped (§4.3). The hosted tests pin the order with a fake that
  completes its reset late (§7.4).
- **Ring fault or a refused Attach:** netdevproto says the connection ends
  (l.69-82). The driver has one outlet, so ending the connection is
  releasing it: every claim then answers `PeerClosed`, which is what a dead
  server looks like (§5.4 item 4 of the networking design). The driver
  then resets, waits for `Status` 0 and unmaps as above, prints the fault by
  `RingError.describe()` with its value and `ring_faults=1`, and exits 2. In
  an image where the driver says `on-exit = "shutdown"` that ends the image
  (Q6).
- **A client that drops its `WaitEvent` claim:** the abandoned delivery
  discards the obligation; the next `WaitEvent` is served (the UART's
  `Abandoned`, l.520-532).

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

[[pool]]
name = "netq"
len = 0x1000

[[pool]]
name = "ring"
len = 0x9000

[[process]]
name = "net"
package = "sawos:drivers/svc-virtio-net"
device = { net = "VIRTIO7" }
pools = { queues = "netq" }
on-exit = "shutdown"

[[process]]
name = "ping"
package = "sawos:tests/net-ping"
clients = { netdev = "net.netdev" }
pools = { ring = "ring" }
```

`tests/net-ping` is a test package written with `main`, depending on `sos`,
`sosmain`, `netdevproto`, `pktring` and `netstack` (`svc-net` is A6). Its
declaration: `system`, a `client` of `netdevproto`, a `memory` record
`ring`; quota `mappings = 1`, `memories = 1`. Its ring is 8 slots each way:
`Layout.bytes()` is 128 + 16 × 2048 = 32,896 bytes, rounded up to the
pool's granularity (`Pool.available`'s set, spec §2.5), 0x9000 on arm64.

What it does:

1. allocate and map the ring, `Endpoint.client`, `Attach` with the Memory in
   handle slot 0; print the reply;
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
value, a hosted test (§7.4) where only a misbehaving device could.

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
| `net_attach_short` | a Memory shorter than the layout | `ShortMemory` | `BadArg`, `a memory shorter than the ring` |
| `net_attach_no_map` | a Memory minted without `Map` | rights checked | `BadArg`, `ring_faults=1` |
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

`Map` answering `NoResource` for a client's Memory (an empty one, one off the
grain, flash) cannot be produced on these boards by a client: every Memory a
pool gives is on the grain and in RAM. It stays covered by `pktring`'s hosted
tests of the attach order and by the spec's own map cases.

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

---

## 8. Size and units

More than one M brief: eight units, one optional. Sizes as §10 of the
networking design uses them (S days, M a week or two).

| Unit | Size | What | Files |
|---|---|---|---|
| **A3-K1** | S | `MappingOp.Physical` (§3.4), after the owner's ruling | `spec.md` §2.5; `kernel/abi/src/ops.saw`, `rights.saw`; `kernel/core/dispatch.saw`; `kernel/sysapi/src/mapping.saw`; `tools/sosimage/src/rights.saw`; `tests/mapping-physical/` and siblings (denied, husk, revoked, the stats row's `BadArg`; two rows of one allocation answer one `Physical` and, on arm64, two `Base`s); the consumer sweep's tests (§3.4); `tools/sos_runner.py` cases |
| **A3-K1b** | S | if Q1 chooses (ii): `dma = true` and the stock-image `Mapping` ceiling (§3.4, §3.6) | `tools/sosimage/src/declaration.saw`, `plan.saw`, its tests; `spec.md` §12; `drivers/svc-virtio-net/process.toml` gains the key in A3-4 |
| **A3-K2** | S | arm64 second device window (§3.5), with the arm64 `virtio_probe` case | `hal/arm64/kernel/lib.saw`, `hal/arm64/kernel/ABI.md`, `hal/arm64/user/ABI.md`; `hal/arm64/board.toml` rows; `tests/images/virtio_probe_arm64.toml`; `tools/sos_runner.py` (the arm64 row of A3-3's probe case) |
| **A3-K3** | M | optional, if Q5 chooses it: critical processes (§4.5) | `kernel/abi`, `kernel/core/sched.saw`, `dispatch.saw`, `process.saw`, `kernel/sysapi`, `tools/sosimage/src/imagefile.saw`, `plan.saw`, `root/`, `spec.md`, `tools/sos_runner.py` |
| **A3-1** | S | `pktring` in-place accessors and cursor rules (§4.2); `netdevproto` doc: `rx_dropped` on a zero-copy driver, the first `WaitEvent` after `Attach`, ending the connection is releasing the outlet | `drivers/pktring/src/lib.saw`, `drivers/pktring/tests/input.saw`, `drivers/netdevproto/src/lib.saw` (doc only) |
| **A3-2** | M | `drivers/virtio`, hosted: MMIO transport v2, negotiation, split queues with the device checks and the failure classes, fake-device tests (§7.4) | `drivers/virtio/` (new); `tools/sos_runner.py` `HOSTED_TESTS` |
| **A3-3** | M | the riscv32 and flat board rows (§2.3); `tools/netpeer` (§6.4-6.5) with hosted tests; the runner's net path (§6.3); `tests/virtio-probe`, a child that maps its window and prints `MagicValue`, `Version`, `DeviceID`, `VendorID` and the config MAC, with cases `virtio_probe` and `virtio_probe_empty` on riscv32 and the flat profile | `hal/riscv32/board.toml`, `hal/riscv32-flat/board.toml`; `tools/netpeer/` (new); `tools/sos_runner.py`; `tests/virtio-probe/`; `tests/images/virtio_probe.toml`, `virtio_probe_flat.toml` |
| **A3-4** | M | `drivers/svc-virtio-net`; `tests/net-ping`; `net_ping`, `net_no_status`, `net_mtu`, `net_legacy`, `net_wrong_device`, `net_no_device`; the spec's DMA sentence (§10) | `drivers/svc-virtio-net/` (new, with `native/fence-riscv32.c`, `native/fence-arm64.c`); `tests/net-ping/`; `tests/images/net_*.toml`; `tools/sosimage/src/imagefile.saw` (the comment on the one-binding refusal, §4.5); `spec.md`; `tools/sos_runner.py` |
| **A3-5** | S-M | `tests/net-client` and the rest of §7.3's table | `tests/net-client/`; `tests/images/net_*.toml`; `tools/sos_runner.py` |

**Files shared.** `tools/sos_runner.py` is touched by A3-K1, A3-K2, A3-K3,
A3-2, A3-3, A3-4 and A3-5, so by the file-overlap rule no two of them run at
once. `spec.md` is touched by A3-K1, A3-K1b, A3-K3 and A3-4; `tools/sosimage`
by A3-K1, A3-K1b, A3-K3 and A3-4; `hal/arm64/board.toml` only by A3-K2.
A3-1 touches none of them, and pairs with any unit.

**Order** (at most two at once):

1. A3-3 (riscv32 and flat), with A3-1 beside it. Both need nothing.
2. A3-2, after A3-3 (the runner).
3. A3-K2, after A3-3: it adds the arm64 rows and the arm64 probe case to
   A3-3's package and runner path.
4. A3-K1 once Q1 is ruled, after A3-K2 (the runner); A3-K1b beside it if
   Q1 chooses (ii) (it touches `tools/sosimage`, which A3-K1 touches only in
   `rights.saw`, so the two are briefed as one unit or run one after the
   other). A3-K3 here too if Q5 chooses it.
5. A3-4, after A3-1, A3-2, A3-3, A3-K1 and A3-K2 (and A3-K3 if chosen). If
   A3-K1 is late, A3-4's riscv32 and flat rows can use `base()` (§3.7) and
   switch to `physical()` when it lands; its arm64 rows wait.
6. A3-5, after A3-4.

---

## 9. Questions for the owner

1. **K1: add `MappingOp.Physical` on a new `MappingRight.Physical` (§3.4),
   and who may learn a row's physical address (§3.6)?** Options: (i) every
   process, a launcher narrowing it where it wants, which a stock image
   cannot; (ii) in stock images only processes whose declaration says
   `dma = true`, the builder narrowing every other child's `Mapping` ceiling
   (A3-K1b, a small builder unit, no further kernel change). Recommended:
   K1 with (ii). Trade-off: (ii) costs a declaration key, a builder rule and
   one `restrict_rights` per child at boot, and makes every DMA driver name
   itself in its package; (i) costs nothing and discloses to every process
   the frames of its own rows only, which riscv32 and the flat profile
   disclose already through `Base`.
2. **Give arm64 processes a second device window, the 2 MiB block at
   0x0A00_0000, at 4 KiB a table set (64 KiB of `.bss`) (§3.5)?**
   Recommended: yes; no arm64 virtio driver can map its transport without it.
3. **Modern virtio-mmio only: the cases pass
   `-global virtio-mmio.force-legacy=false`, and the driver refuses a version
   1 transport (§1.1)?** Recommended: yes.
4. **Zero copy: descriptors point at the client's `pktring` slots, through
   new in-place accessors (§4.2), rather than the driver copying each frame?**
   Recommended: yes. The driver acts only on index and length words, read
   once and checked; the device touches the bytes.
5. **The driver's death (§4.5).** A3 rests on the argument that in a stock
   image the bytes a dead driver's device still addresses are never handed
   out again (one holder per pool, given whole; boot pools and boot
   allocations return their bytes nowhere), so the driver's death needs no
   halt ahead of the free. Options: (a) accept the argument for stock
   images, record it in the spec (§10), keep `on-exit = "shutdown"` as the
   image's policy, and schedule critical processes when driver restart or
   sub-pools are; (b) build critical processes (A3-K3, M) first and mark the
   driver critical. Recommended: (a). Trade-off: (a) adds no unit but holds
   only while the builder's one-binding rule and the boot-pool rule hold, and
   a hand-written root can break it; (b) is general and puts an M kernel and
   builder unit in front of A3-4.
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

---

## 10. Spec changes proposed

| Where | Change | Unit | Question |
|---|---|---|---|
| spec.md §2.5, Mapping (l.1430-1452) | "Five ops"; `Physical` on `MappingRight.Physical` (bit 11), text in §3.4, the stats row included | A3-K1 | Q1 |
| spec.md §2.5, l.1473 | `mapping_rights()` mints `Physical` too | A3-K1 | Q1 |
| spec.md §2.5, l.1506 | `Physical` among the ops a dead target's Mapping answers `Revoked` | A3-K1 | Q1 |
| spec.md §2.5, "A region is a capability" (l.1020-1031) | names `Physical` beside `Base` and `Len` | A3-K1 | Q1 |
| spec.md §2.5, DMA paragraph (l.1558-1574) | a DMA driver learns bus addresses from its rows (`Mapping.Physical`). The last sentence, "no case in the suite is a DMA driver and no capability in the tree is a lie about one", becomes: "`svc-virtio-net` on the QEMU `virt` boards is the suite's DMA driver, and a TCB member. Its device is given addresses only in its own queue allocation and its client's ring; in an image the stock root launches each pool has one holder and is given whole, and a boot pool or boot allocation whose holders are gone returns its bytes nowhere, so those bytes are never handed out again after the driver dies, and its death needs no critical-process halt. A launcher that shares or keeps a DMA driver's pool, or restarts the driver, needs one." | A3-4 | Q5 |
| spec.md §12 (l.4790 on) | with Q1 (ii): the stock root narrows `Physical` out of every child's `Mapping` ceiling but those whose declaration says `dma = true` | A3-K1b | Q1 |
| spec.md §2.7, §8 and the DMA paragraph | with Q5 (b): critical processes | A3-K3 | Q5 |
| `hal/arm64/kernel/ABI.md`, `hal/arm64/user/ABI.md` | two device blocks, not one | A3-K2 | Q2 |
| `drivers/netdevproto` docs (not the spec) | `rx_dropped` on a zero-copy driver; the first `WaitEvent`; ending a connection | A3-1 | Q6 |

---

## 11. What was checked, how

| Fact | How |
|---|---|
| transports, addresses, lines, `dma-coherent` (every arm64 transport node, no riscv32 one), edge flag on arm64 | `-M virt,dumpdtb=...` with a `virtio-net-device` on both machines, `dtc -I dtb -O dts` (`so103a3/exp1.sh`, `rv.dts`, `a64.dts`) |
| default bus placement; `force-legacy = true`; region 0x200 | HMP `info qtree` (exp1) |
| `Version` 1 by default, 2 with `force-legacy=false`; `DeviceID` 1 at the device, 0 elsewhere; `bus=virtio-mmio-bus.0` places at 0x1000_1000 | HMP `xp /4wx` on a paused machine (exp1) |
| QEMU's offered config (`mac`, `status` = 1, nothing past), `QueueNumMax` 256 | HMP `xp` at 0x10008100 and 0x10008034 with `force-legacy=false` (`so103a3/exp2.sh`) |
| `host_mtu` offering `MTU` | QEMU's source, `virtio_net_device_realize`: `if (n->net_conf.mtu) n->host_features \|= (1ULL << VIRTIO_NET_F_MTU);` and `DEFINE_PROP_UINT16("host_mtu", VirtIONet, net_conf.mtu, 0)`. `so103a3/exp5.sh` reads the config with `host_mtu=1400`; it has not run, since the suite lock was held by another session throughout revision 2, and the A3-4 implementer runs it before writing `net_mtu`'s expected text |
| `virtio-net-device` properties and defaults (`status`, `mrg_rxbuf`, `host_mtu`, `iommu_platform`, `mq`, `packed`) | `-device virtio-net-device,help` |
| `-netdev stream` syntax (`inet` and `fd`), the 4-byte length framing, `connecting` at start, the NIC's link bit independent of the stream | `so103a3/exp3.py` with `filter-dump` |
| paused start, `NETDEV_STREAM_CONNECTED`, `prelaunch` then `cont`; `-object monitor-qmp` | `so103a3/exp4.py`, `exp4b.py` |
| QEMU source facts (irq level, `force-legacy` default, reset, ACK, no `VERSION_1` enforcement; RX drop of a frame that does not fit, header length, link status notify; stream `link_down` and framing; the PLIC's pending rule) | QEMU master `hw/virtio/virtio-mmio.c`, `hw/net/virtio-net.c`, `hw/intc/sifive_plic.c`, `net/stream.c`, `net/stream_data.c`, read online; line numbers approximate |
| virtio 1.2 sections and quotes | OASIS virtio-v1.2-cs01 HTML, downloaded and converted to text (`so103a3/spec/v.txt`); every section number and quote here was found in it: §1.4, §2.1, §2.2.1, §2.4.1-2.4.2, §2.7, §2.7.4, §2.7.10.1, §2.7.13, §2.7.13.4.1, §2.7.14, §3.1.1, §4.2.2-4.2.4, §5.1.1-5.1.6, §6, §6.1 |
| arm64 grant and device windows, table pool, RAM attributes | `hal/arm64/kernel/lib.saw` l.488-608, l.1085-1360, l.2060-2100 |
| kernel Mapping ops, grant row's `pa`, the teardown's order and exit line | `kernel/core/dispatch.saw` l.3620-3760, `kernel/core/pslot.saw` l.116-119, `kernel/abi/src/ops.saw` l.809-853, `rights.saw` l.750-768, l.1264-1267, `kernel/core/sched.saw` l.641-700 |
| builder binding of devices, pools, inputs; the one-binding rule; the plan's `Source.Row` | `tools/sosimage/src/imagefile.saw` l.1-80, l.455-490, l.815-1120; `plan.saw` l.455-520; `declaration.saw` l.1-120; `board.saw` l.1-120, l.655-720 |
| the netstack's time use | `drivers/netstack/src/lib.saw` l.20-27, l.287-334, l.421-470, l.690-698; `arp.saw` l.99-226 |

Each experiment ran under the sawos suite lock, taken with `mkdir` and given
back with `rm owner; rmdir`, only after a successful `mkdir`.
