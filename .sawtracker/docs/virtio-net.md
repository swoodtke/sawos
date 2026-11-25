# SO-103 A3: `svc-virtio-net`, the runner's network peer, and `net_ping`

Design for unit A3 of the networking design (`.leadwork/networking.md`,
revision 2, §10 l.1385-1390). Design only: nothing here changes the tree.
The facts it rests on were checked against the code, the spec, the virtio 1.2
specification (OASIS, cs01), QEMU 11.1.3 as installed (`qemu-system-riscv32`,
`qemu-system-aarch64`) and QEMU's source (master, `hw/virtio/virtio-mmio.c`,
`hw/net/virtio-net.c`, `net/stream.c`, `net/stream_data.c`). The experiments
are in the scratch directory (`so103a3/exp1.sh` to `exp4b.py`); §11 lists
them.

The short version:

- Modern virtio-mmio (version 2), negotiated features `VERSION_1` and `MAC`
  required, `STATUS` taken when offered, nothing else; a 12-byte header in a
  descriptor of its own, frames zero-copy in the client's `pktring` slots.
- The board lists each virtio-mmio transport as a device of type
  `virtio-mmio`; the image names the one the driver takes; the driver checks
  `DeviceID` and refuses anything but a network device.
- riscv32 and the flat profile need no kernel change. arm64 needs two: a
  Mapping op that answers a row's physical address (`MappingOp.Physical`), and
  a second device window in the arm64 HAL, since the transports lie outside
  the one 2 MiB block a process can be granted device pages in today.
- The peer is a Saw tool (`tools/netpeer`), one process per case on a
  loopback port of its own; QEMU connects to it with `-netdev stream` and is
  started paused and resumed only once QMP says the stream is connected.
- Seven units: two small kernel units, one small `pktring` unit, three
  medium units and a small-to-medium unit of red cases (§9).

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
- Modern has `FEATURES_OK` (§2.1, §3.1.1 step 5-6), so a device that cannot
  run with the driver's subset says so before the driver goes live.
- Modern is little-endian always and its net header is fixed at 12 bytes
  (§1.3, §5.1.6.3); legacy's header length depends on `MRG_RXBUF`.
- The driver refuses a version-1 transport by name (§7.3, `net_legacy`).

### 1.2 Features

Feature bits from virtio 1.2: §5.1.3 for the network device, §6 for the
reserved bits. (The published HTML truncates before §6; the reserved bit
numbers below are the spec's, quoted from §6.1 by name, and the implementer
reads them off the TeX source or Linux's `virtio_config.h` when writing the
constants. QEMU's offer was read in exp2.)

| Bit | Name | Decision | Why |
|---|---|---|---|
| 32 | `VERSION_1` | **required** | a modern driver; §6.2 "a driver MUST accept VIRTIO_F_VERSION_1 if it is offered" |
| 5 | `MAC` | **required** | the MAC the Attach reply carries (netdevproto `AttachInfo.mac`); §5.1.4.2 "SHOULD negotiate VIRTIO_NET_F_MAC if the device offers it". QEMU always offers it (no property turns it off) |
| 16 | `STATUS` | **taken when offered** | link up and down (§5.3). Without it the driver reports the link up, as §5.1.4.2 says: "If the driver does not negotiate VIRTIO_NET_F_STATUS, it SHOULD assume the link is active" |
| 0, 1 | `CSUM`, `GUEST_CSUM` | declined | the stack computes and checks every checksum (`drivers/netstack`); flags stay 0 (§5.1.6.2.1) |
| 6-14, 56-63 | GSO, TSO, UFO, ECN, USO, RSC, hash, RSS | declined | one frame per buffer, no offload |
| 15 | `MRG_RXBUF` | declined | one 2048-byte slot holds a whole frame; without it the device "MUST set num_buffers to 1" (§5.1.6.4.1) and uses one descriptor chain per frame (§5.1.6.3.2) |
| 17 | `CTRL_VQ` (and `CTRL_RX`, `MQ`, `CTRL_MAC_ADDR`, ...) | declined | no control queue, so one queue pair (§5.1.2: "N=1 if neither VIRTIO_NET_F_MQ nor VIRTIO_NET_F_RSS are negotiated"); without `CTRL_RX` QEMU delivers every frame, and the stack's own filter drops what is not ours (`netstack` lib.saw l.37-41) |
| 3 | `MTU` | declined | QEMU offers it only with `host_mtu` set (default 0, `virtio-net-device,help`); the Attach reply's MTU is Ethernet's 1500 (`ether.MTU`) |
| 28, 29 | `INDIRECT_DESC`, `EVENT_IDX` | declined | two-descriptor chains are enough; notification by `used.flags` (§4.4) |
| 33 | `ACCESS_PLATFORM` | declined | addresses are physical (§3); QEMU offers it only with `iommu_platform=on` (default off) |
| 34-38 | `RING_PACKED`, `IN_ORDER`, `ORDER_PLATFORM`, `SR_IOV`, `NOTIFICATION_DATA` | declined | split queue; the driver handles completions out of order (§4.3) |

The negotiated word on QEMU's defaults is `VERSION_1 | STATUS | MAC`,
`0x1_0001_0020`. Exp2 read the config space with that offer: the MAC
`52:54:00:12:34:56` at 0x100 and `status` 0x0001 (`VIRTIO_NET_S_LINK_UP`,
§5.1.4) at 0x106, nothing past it (`0xffffffff`), so neither `MQ` nor `MTU`
was offered.

`QueueNumMax` reads 256 on both queues (exp2, `xp 0x10008034`).

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
- The header and the frame are two descriptors of one chain. §5.1.6.2 step 5
  describes "one output descriptor", but a `VERSION_1` device makes no
  assumption about how a message is split across descriptors (§2.6.4,
  Message Framing), and QEMU reads the header from the first element and
  refuses only a chain with no element at all (`virtio_net_flush_tx`
  ~l.2350, "virtio-net header not in first element" when `out_num < 1`).

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
| An empty transport | `DeviceID` 0 (`xp 0x10007000`, exp1); spec §4.2.3.1.1: "If DeviceID is 0x0, it MUST abort and MUST NOT access any other register" | the same |

The board files already number lines as the interrupt controller does: the
arm64 console is `line = 33`, its SPI 1 (`hal/arm64/board.toml`).

QEMU drives a transport's line as a level, `level = (isr != 0)`
(`virtio-mmio.c` `virtio_mmio_update_irq` ~l.455-465), and lowers it when the
driver writes `InterruptACK` (~l.345). The arm64 device tree marks the SPIs
edge-triggered (`a64.dts` l.41), but the HAL never writes `GICD_ICFGR`
(`hal/arm64/kernel/lib.saw` l.2067-2079, `reset`), so the lines stay
level-sensitive, as the PL011's is. Spec §9's mask-on-fire, ack-to-rearm
(spec.md l.4398-4402) is then exact for virtio: a line still high at
`irq.ack()` fires again.

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

The driver reads `MagicValue`, `Version` and `DeviceID` before touching
anything else (§4.2.3.1.1) and refuses, with a line naming what it read:

- a `MagicValue` other than `0x74726976`;
- a `Version` other than 2 (a legacy transport);
- a `DeviceID` of 0 (no device; the spec says to report no error, but a
  driver an image bound to this transport has nothing else to do, so it says
  so and exits);
- any `DeviceID` other than 1.

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
handles = 20
```

Every record kind exists today (`tools/sosimage/src/declaration.saw` l.1-45:
`window`, `line`, `serve`, `memory`; a `memory` record is bound to an image
pool by `pools`, l.37-41 of `imagefile.saw`), and the stock root gives each
(`root/bootplan/src/lib.saw` l.103-127). No builder or root change is needed.

Handles, at their most: Process, main Thread, System, window, line, pool,
queue Memory, three Mappings, Waiter, the held `WaitEvent` request, the
request being served and the `PIPE_MSG_HANDLES` (4,
`kernel/abi/src/ops.saw` l.1550) it may carry: 17. Twenty leaves the margin
the GPIO driver's comment asks for (`drivers/svc-gpio-esp32s3/process.toml`,
`[quota]`); the implementer writes the count into the comment, as there.

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
1.2 §2.7.5, `le64 addr`). With `ACCESS_PLATFORM` not negotiated "the device
will always use physical addresses matching addresses used by the driver
(typically meaning physical addresses used by the CPU)" (§6.1; quoted from
the spec's text as known, since the published HTML truncates before §6, so
the A3-2 implementer checks the wording against the TeX source). QEMU's `virt`
machines have no IOMMU in front of virtio-mmio, so the address is the guest
physical address. The driver needs two:

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
| arm64 (MMU, tier 1) | at the map cursor, inside the 4 MiB grant window from `RAM_BASE` (`hal/arm64/kernel/lib.saw` l.494-499 `map_place`, l.588 `map_target_ok`, l.2326 `USER_RAM_TOP`), backed by frames anywhere in RAM (l.528-530 `map_source_ok`) | a user address that is not the frame's | **no** |

On arm64 no op answers it. The spec says so on purpose: "There is no op on a
pool or an allocation that reads its bounds ... a `Mapping` answers where its
row is and how long it is (`Base`, `Len`) ... Where a row's address is its
memory's own, those two are the allocation's physical range" (spec
l.1020-1031). The kernel has the number: every grant row records its
physical base beside its user range (`kernel/core/pslot.saw` l.116-119,
`GrantRow.pa`; spec l.3279-3284 "`GrantRow` carries a user VA range and the
physical base it translates to").

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
l.3600-3609). The console is at 0x0900_0000; the transports are at
0x0A00_0000, another block. So on arm64 the driver's `map(iomemory:)` faults
today, whatever is decided about addresses.

### 3.4 Proposal K1: `MappingOp.Physical`

The smallest change that answers the question everywhere with one spelling.

- **Op:** `MappingOp.Physical = 4`: the physical address of the row's first
  byte, read from the grant record (`GrantRow.pa`), in the value register.
  The dispatch arm is `Base`'s with `pa` for `base`
  (`kernel/core/dispatch.saw` l.3723-3734).
- **Right:** `MappingRight.Physical = 1 << 11`, the next free kind bit after
  `Grow` (`kernel/abi/src/rights.saw` l.750-768). `mapping_rights()` mints
  it (l.1264-1267), so every `map` answers a Mapping that carries it, and a
  launcher withholds it with a `mapping` ceiling (`[ceilings] mapping = [...]`,
  `declaration.saw` l.47-62; spec §2.7). Attenuation is monotonic (§3), so a
  bit not minted by `map` could never reach a driver at all; that is why it is
  minted by default.
- **Refusals:** `Base`'s, in `Base`'s order (`mapping_row_asked`,
  dispatch.saw l.3637-3650): no `Physical` right is the `AccessDenied` fault,
  a freed slot or an unmapped husk the `BadState` fault, a husk whose target
  died `Revoked`.
- **API:** `sos.Mapping.physical() -> Result<UInt, SosStatus>` beside
  `base()` and `len()` (`kernel/sysapi/src/mapping.saw` l.104-130).
- **Names:** `tools/sosimage/src/rights.saw` learns `physical` among the
  mapping rights, so a declaration's ceiling can name it.

Spec text, to add to §2.5's `Mapping` list after `Len` (l.1441-1452), with
"Four ops" becoming "Five ops":

> `Physical` (on `MappingRight.Physical`, bit 11) answers the physical
> address of the row's first byte: where the bus finds the bytes the row
> grants, which is what a device that masters the bus is given. On a tier
> that does not translate it is the number `Base` answers; on tier 1 it is
> the frame the row's first page translates to. A row covers one
> allocation, which is one physically contiguous run, so the row's bytes are
> the run from this address for `Len` bytes. A device window's row answers
> the window's own address. Its refusals are `Base`'s, in `Base`'s order,
> with `Physical`'s right in place of `Base`'s. `mapping_rights()` mints it;
> a launcher that does not want a child to learn where its memory lies
> narrows the child's `Mapping` ceiling.

and to the paragraph at l.1020-1031, after "(`Base`, `Len`, below)": "and,
for a driver that hands the bytes to a device, where they are on the bus
(`Physical`)". The DMA paragraph (l.1558-1570) gains one sentence: "Such a
driver learns the bus address of memory it has mapped from the row
(`Mapping.Physical`)".

**What it exposes, and why that is acceptable.** It tells the holder of a row
which physical frames back memory it can already read and write. On riscv32
and the flat profile `Base` tells it that already. On arm64 it reveals the
physical layout of the holder's own rows and nothing about anyone else's.
The number grants nothing: using it takes a bus master, and the spec already
puts any driver of a DMA-capable device inside the TCB (l.1558-1566: "the
window it was granted is a fence around the driver, never around the
device"). The authority is the device window, which the image gives; the
number is a fact that driver needs to use it.

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

| Alternative | Kernel change | Cost | Verdict |
|---|---|---|---|
| **K1** `MappingOp.Physical` | one op, one right bit, ~15 lines of dispatch, a sysapi method, a ceiling name, spec text | none at run time | **recommended** |
| Identity-mapped RAM rows on arm64 (a `map` flag that places a row at its frame's own address, so `Base` is physical everywhere) | a new `MapAccess` bit and a second placement rule | a RAM row's user address must lie in the 4 MiB grant window at `RAM_BASE` (l.588), and frames come from all 126 MiB, so most allocations could not be placed; the grant window would have to become all of RAM's addresses, a redesign of the arm64 tables | rejected |
| The launcher tells the driver its pool's physical base (a builder-written build input) and the driver copies frames between the client's ring and its own buffers | none (K2 still needed) | the builder learns to write an input whose value it computes; the driver relies on its first allocation sitting at its pool's front (spec l.1163, which then becomes a contract an address depends on); every frame copied once more in each direction; the arm64 driver differs from the riscv32 one; nothing for a client's memory, ever | rejected |
| Zero copy on riscv32 only, copying on arm64 | the above | two drivers in one | rejected |

K2 is needed by every alternative: without it no arm64 driver can map a
transport.

### 3.7 What the driver does with it

At start: map the queue allocation, read `physical()` and `base()`; every
queue address the driver writes to the device is `physical + offset`, every
CPU access `base + offset`. At `Attach`: map the client's Memory, read
`len()` (the ring check, `pktring` doc l.69-94) and `physical()`; a slot's bus
address is `physical + Layout.slot_offset(ring, slot)` (`pktring` l.257-266).
The offsets come from the driver's private layout and checked indexes (§4.2),
never from shared memory.

If the owner declines K1, riscv32 and the flat profile still work with
`base()`, which the spec defines to be physical there, and arm64's A3 rows
wait.

---

## 4. Queues and buffers

### 4.1 The queue memory

One allocation from the driver's pool (`memory` record `queues`, image pool
`netq`, `len = 0x1000`), mapped read-write into the driver and zeroed by it
(§4.2.3.2 step 4: "Allocate and zero the queue memory"; spec §2.5 promises no
zeroing). `QUEUE_SIZE = 64` descriptors a queue, a power of two (§2.7) and at
most `QueueNumMax` (256, exp2). A `QueueNumMax` below 64 is taken as it is if
it is a power of two of at least 2; 0 or 1 is refused by name
(§4.2.3.2 step 3: 0 means the queue is unavailable).

Layout at 64, offsets from the first 16-aligned byte of the allocation (on
riscv32 an allocation is only 4-aligned, the grain, so the driver rounds the
physical base up; 16 bytes of the page are slack):

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
(frame), so 32 frames a queue can be in flight and a used element's `id` must
be an even number below `QUEUE_SIZE` naming a chain in flight. The driver
writes each `addr` and `len` once per posting; flags are `NEXT` and `WRITE`
for an RX header, `WRITE` for an RX frame, `NEXT` for the TX header, 0 for a
TX frame (§2.7.5).

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
| the same length, as a frame | from the private copy | at least `ETHERNET_HEADER_BYTES` (14) and at most 14 + 1500 | not a ring fault: the frame is not sent, `tx_errors` counts it, and the slot completes as sent |
| ToClient tail | at an `RxReturn` doorbell and when the driver looks for free RX slots | below `2n`, and in order (`read_out_tail`, l.652-665) | ring fault |
| a doorbell's range | on the wire | `SlotRange.fits` (`pktring` l.333-336, applied on the wire by `netdevproto` l.486-491) | `BadArg`; not acted on either way |

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
  resets the device before it unmaps (§5.4). A reset stops the device using
  every queue: "The device MUST reinitialize device status to 0 after
  receiving a reset" (§2.4.2), and QEMU's soft reset clears each queue's
  `enabled` (`virtio-mmio.c` ~l.72-82, ~l.370-395). After that the device
  holds no address in the ring.
- The driver can die with buffers posted. Its queue memory then goes back
  to its pool while the device still holds its addresses. This is rule 5.1
  (networking.md l.954-959) for this device: until a restart unit exists, the
  driver's death ends the image. The image says `on-exit = "shutdown"` for the
  driver (`root/src/main.saw` l.43-45). The client's ring is the client's own
  memory and stays allocated to it while it lives.

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
    /// its length word read once and checked against the slot size. The
    /// slot stays this end's until `release(count:)` gives it back.
    public func take(&var self) -> Result<SlotRef, RingError>

    /// Gives back the oldest `count` slots taken and not yet released, and
    /// answers them for the doorbell. More than are taken is a caller's
    /// bug, and panics.
    public func release(count: UInt32) -> Result<SlotRange, RingError>

    /// The next free slot of the ring this end fills, without writing it:
    /// the peer's tail read afresh only when every slot it last freed is
    /// reserved. `Full` when none is free.
    public func reserve(&var self) -> Result<SlotRef, RingError>

    /// Writes `len` as the length word of the oldest reserved slot not yet
    /// filled, marking it filled; `publish` then shows it to the peer.
    /// `len` past the slot size is `TooLong`.
    public func fill(&var self, len: UInt) -> Result<UInt32, RingError>
}
```

`take` latches and answers the same errors as `receive`; `reserve` the same
as `send`'s room check. `SlotRef.len` is 0 from `reserve` (the device writes
the bytes; the driver states the length at `fill`). Hosted tests in
`drivers/pktring/tests/input.saw` hold each to rule 5.2 as A1's tests hold
`receive`: a length word changed between two reads is read once; an index
past the count; out of order; `release(count:)` past what is taken panics.

### 4.3 The device is checked too

The device is QEMU here, but a driver that trusts a used ring blindly would
let a buggy device steer it. A device value that fails a check is fatal:
the driver resets the device, prints the check and the value, and exits 3,
which ends the image.

| Device value | Check (virtio 1.2) |
|---|---|
| `MagicValue`, `Version`, `DeviceID` | §2.4 above |
| offered features | `VERSION_1` and `MAC` present (§1.2) |
| status after `FEATURES_OK` | the bit still set (§3.1.1 step 6, §2.2.2) |
| `QueueReady` before setup | 0 (§4.2.3.2 step 2) |
| `QueueNumMax` | §4.1 |
| `ConfigGeneration` | equal before and after reading `mac` and `status`, else read again (§4.2.2, `ConfigGeneration` at 0x0fc) |
| used `idx` | advanced by no more than the chains in flight (`idx` is mod 2^16) |
| used element `id` | even, below `QUEUE_SIZE`, a chain in flight (§2.7.8) |
| RX used `len` | at least 12 and at most 12 + the slot size (§2.7.8.2: the device "MUST write at least len bytes"; a value past the buffer is a device bug) |
| RX header `num_buffers` | 1 (§5.1.6.4.1) |
| `DEVICE_NEEDS_RESET` in status at a configuration change | fatal, by name (§2.1.2) |

An RX frame shorter than 14 bytes is not a device fault: it is published as
received and the client's stack drops it (`netstack` ether.saw l.50-52,
`Drop.EtherRunt`). The driver judges no frame's content.

The order of the driver's own work: descriptors and ring entries written, a
barrier, `avail.idx` written, a barrier, then `QueueNotify` with the queue's
index (§2.7.13 "Supplying Buffers to The Device"; §4.2.3.3); `used.idx`
read, a barrier, then the used elements (§2.7.14). Notify only when the used
ring's `flags` is 0 (§2.7.10.1, without `EVENT_IDX`: "If flags is 1, the
driver SHOULD NOT send a notification. If flags is 0, the driver MUST send a
notification").

The barrier is a two-line C file per architecture in the driver's `native`
(`fence iorw, iorw` on riscv32, `dsb sy` on arm64). Saw's `Device` accesses
are volatile and "Volatile is not atomic — it constrains the compiler, not
the memory system; ordering across accesses still needs fences" (sawlang
LANGUAGE_SPEC.md l.17246-17252), and the runtime offers no fence a process
can call. The hosted library takes the barrier as a trait method, so its
tests check where it is called.

### 4.4 Interrupts

One task owns the Interrupt (spec §9 l.4413-4416). On its delivery:

1. read `InterruptStatus`;
2. write the bits read to `InterruptACK` (§4.2.3.4.1), which lowers the line;
3. on bit 1 (configuration change), re-read `status` under
   `ConfigGeneration`; a changed `LINK_UP` bit queues a link event;
   `DEVICE_NEEDS_RESET` is fatal (§4.3);
4. drain both used rings (whatever bit 0 said): TX completions, then RX
   completions, publishing filled RX slots in ring order;
5. post RX buffers into every ToClient slot the client has freed, notify
   RX if any were posted;
6. answer the held `WaitEvent` if an event is pending;
7. `irq.ack()`.

Every device register access and used-ring read is before the ack (spec §9
"Ack is a release", l.4403-4410). A completion that lands after step 2
raises the line again, and the kernel delivers it after step 7, so none is
lost (§9 l.4398-4402).

Out of order: QEMU completes in order, but nothing promises it without
`IN_ORDER`. TX slots are released in ring order (`release(count:)` over the
completed prefix); RX slots are filled and published in ring order
(`fill` for the completed prefix). A flag per chain is the bookkeeping.

---

## 5. The netdevproto server

The driver is a completion-queue server like the UART drivers
(`drivers/svc-uart-ns16550/src/main.saw` l.1-45): the outlet is given to the
Waiter (consuming, persistent), the Interrupt and a held `WaitEvent`
obligation are attached, and every delivery arrives through `wait`.

### 5.1 Start

Map the window (`map(iomemory:)`), check the transport (§2.4), reset and wait
until `Status` reads 0 (§2.4.2), set `ACKNOWLEDGE` and `DRIVER`, negotiate
(§1.2), `FEATURES_OK` and check it, allocate and zero the queues, set them up,
read `mac` and `status`, set `DRIVER_OK` (§3.1.1). The RX queue is empty
until a client attaches: its buffers are the client's slots. QEMU holds
frames that arrive with no buffer posted (`virtio_net_has_buffers` returns 0,
`virtio-net.c` ~l.2010), and the stream backend stops reading its socket, so
nothing is lost before `Attach`.

Two lines, through the debug print (no console record, as the UART drivers):

```
SOS netdrv: virtio-mmio version 2, network device, features version_1 mac status
SOS netdrv: mac 52:54:00:12:34:56, link up, queues 64 and 64
```

(`status` is printed only when negotiated; `link up` reads `link assumed up`
without it.)

### 5.2 The ops

| Op | What the driver does |
|---|---|
| `Attach(to_server, to_client)` + Memory | checks in `netdevproto`'s order (l.61-67). The Memory's rights hold `Map` (`rights()`), else `BadArg`, counted, connection ended. `map` it read-write; `NoResource` (spec §2.5: empty, off the grain, flash) is counted and ends the connection. `SharedWindow.init(at:len:layout:)` with `len()`; `ShortMemory` likewise. Then `physical()`, `Endpoint.server`, post RX buffers into the free ToClient slots (all of them, up to 32), notify, answer `AttachInfo` (mac, mtu 1500, the request's counts). The next `WaitEvent` is answered at once with the link's state. A second `Attach` is `Attached` |
| `Tx(range)` (a tell) | `poll` the ToServer ring; for each new slot while a TX chain is free: `take`, check the frame length (§4.2), post or complete with `tx_errors`; notify. Slots past 32 in flight wait in the ring until chains free. Answered `Ok` (and discarded, as the UART's `Write`) |
| `RxReturn(range)` (a tell) | `reserve` every freed ToClient slot while an RX chain is free, post, notify |
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
  (the UART's `Readable`). The driver resets the device (§4.2), then unmaps
  the ring, prints, and exits 0:

  ```
  SOS netdrv: client gone: device reset, ring unmapped
  SOS netdrv: rx=4 tx=5 tx_errors=0 ring_faults=0
  ```

  The order, reset before unmap, is what keeps the device off memory the
  client's pool could hand out again; the hosted tests pin it (§7.4).
- **Ring fault or a refused Attach:** netdevproto says the connection ends
  (l.69-82). The driver has one outlet, so ending the connection is
  releasing it: every claim then answers `PeerClosed`, which is what a dead
  server looks like (§5.4 item 4 of the networking design). The driver
  resets the device, unmaps, prints the fault by `RingError.describe()` with
  its value and `ring_faults=1`, and exits 0. In an image where the driver
  says `on-exit = "shutdown"` that ends the image (Q6).
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
   plus the case's `props` (`status=off`). `N` is the arch's
   `net_bus` (7 on riscv32 and the flat profile, 24 on arm64), a new key in
   `ARCHES` next to the board rows it matches (§2.3). The case's `device`
   replaces `virtio-net-device` for the wrong-device refusal
   (`virtio-rng-device`), and `none` leaves it out.
3. QMP: greeting, `qmp_capabilities`, the connected check of §6.1, `cont`.
4. Serial as `_run_qemu` does it (an empty pipe held open), stdout captured,
   the same timeout. A case's `on_console` pairs (a console line, a QMP
   command) send `set_link net0 on` when the line appears, for the link case
   (§7.3); the runner reads stdout as it arrives for these cases.
5. After QEMU exits, read the peer's stdout to its end and wait for it (it
   exits when QEMU's end of the stream closes), bounded by the same timeout.
   A peer that exits non-zero fails the case with its stderr.
6. Match `expect_out` against the console as today, and `expect_peer`, the
   peer's lines in order, exactly.

`files(name)` (l.2541-2544) gives the three image descriptions, so a net
case is an `image_file` case like `console_service`.

### 6.4 What the peer answers

The peer is at 10.0.2.2 with MAC `52:55:0a:00:02:02`; the guest is 10.0.2.15
on 10.0.2.0/24 with gateway 10.0.2.2 (QEMU's user-network numbers, familiar
to a reader of a transcript; nothing reaches QEMU's own user network).

| Guest frame | Script | Peer's answer | Log line |
|---|---|---|---|
| ARP request, sender = target = guest (gratuitous) | all | none | `peer: arp announce 10.0.2.15 at 52:54:00:12:34:56` |
| ARP request for 10.0.2.2 | all | ARP reply | `peer: arp request for 10.0.2.2 from 10.0.2.15 at 52:54:00:12:34:56, answered` |
| ICMP echo request to 10.0.2.2, checksums good | `ping`, `burst` | echo reply, same id, sequence and data | `peer: echo request 1 from 10.0.2.15, 56 bytes, answered` |
| UDP to 10.0.2.2:7 carrying `burst N` | `burst` | `N` UDP datagrams to the sender's port, payload `frame K of N` | `peer: burst of N to 10.0.2.15:PORT` |
| anything else, or a bad checksum, length or address | all | none | `peer: unexpected: ...` naming it |
| any frame | `silent` | none | `peer: frame N bytes` |

At its end: `peer: closed after N frames`. The peer encodes and checks frames
with its own code, not `netstack`'s, so a symmetric encoding bug in the stack
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
- a blocking read and write of one stream: `TcpStream.read`, `write`
  (l.855-990, `read` at l.855, `write` at l.952 and l.977);
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

Console (`expect_out`, the same on all three profiles):

```
{banner}
SOS netdrv: virtio-mmio version 2, network device, features version_1 mac status
SOS netdrv: mac 52:54:00:12:34:56, link up, queues 64 and 64
SOS netping: attached, mac 52:54:00:12:34:56, mtu 1500, slots 8 and 8
SOS netping: link up
SOS netping: 10.0.2.15 pinging 10.0.2.2
SOS netping: reply 1 from 10.0.2.2, 56 bytes
SOS netping: reply 2 from 10.0.2.2, 56 bytes
SOS netping: reply 3 from 10.0.2.2, 56 bytes
SOS netping: driver rx=4 rx_dropped=0 tx=5 tx_errors=0 ring_faults=0
SOS netping: stack drops=0
root: ping ended status=65536
SOS netdrv: client gone: device reset, ring unmapped
SOS netdrv: rx=4 tx=5 tx_errors=0 ring_faults=0
root: net ended status=65536
```

with `expect_clean_exit` (root stops the machine with the driver's exit code
0, `root/src/main.saw` l.43-45). TX is the gratuitous ARP, the ARP request
and three echo requests; RX the ARP reply and three echo replies. The
implementer confirms the order of the two driver lines against the pinger's
on the first run: with no tick, root starts the driver first and the driver
runs until it waits.

Peer (`expect_peer`):

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
denial, so the flat profile excludes none):

| Case | Image / QEMU | Check it holds | Expected |
|---|---|---|---|
| `net_ping` | §7.1 | the whole path | §7.2 |
| `net_no_status` | `props = "status=off"` | `STATUS` optional (§1.2) | `features version_1 mac`, `link assumed up`, the three replies |
| `net_legacy` | no `force-legacy=false` | `Version` 2 | `SOS netdrv: refused: virtio-mmio version 1, not 2`; driver exits 1, machine status 1 |
| `net_wrong_device` | `device = "virtio-rng-device"` | `DeviceID` 1 | `refused: device 4, not a network device` |
| `net_no_device` | `device = "none"` | `DeviceID` 0 | `refused: no device at this transport` |
| `net_link_down_first` | `set_link net0 off` before `cont`; `on_console` `SOS netclient: waiting for link` → `set_link net0 on` | link state from `STATUS`, the configuration-change interrupt | first event `LinkDown`, then `LinkUp`, then a ping answered |
| `net_tx_length` | client sends a 2000-byte frame (within the slot, past 1514), then a 10-byte one, then pings | frame length check | `tx_errors=2`; peer log has no frame of either length (`silent` script plus the ping rows) |
| `net_ring_index_past` | client stores ToServer head = 2n + 3, rings `Tx`, then calls `Stats` | ToServer head below 2n | driver: `connection ended: an index past the slot count: 19, ring_faults=1`; client's call answers `PeerClosed` |
| `net_ring_index_order` | head moved back | in order | `an index out of order` |
| `net_ring_length_past` | a length word of 4096 | length against slot | `a length past the slot: 4096` |
| `net_ring_tail_past` | ToClient tail = 2n + 1, rings `RxReturn` | ToClient tail checked | `an index past the slot count` |
| `net_attach_short` | a Memory shorter than the layout | `ShortMemory` | `BadArg`, `a memory shorter than the ring` |
| `net_attach_no_map` | a Memory minted without `Map` | rights checked | `BadArg`, `ring_faults=1` |
| `net_protocol_states` | `Tx` before `Attach`, `Attach` twice, `WaitEvent` twice, a range that does not fit | statuses in order | `NotAttached`, `Attached`, `Busy`, `BadArg` |
| `net_slow_client` | `burst` script: client sends `burst 20`, holds every slot until 8 have arrived, then returns slots one at a time | the driver posts only freed slots, publishes in order | the client receives `frame 1 of 20` to `frame 20 of 20` in order, intact; `rx_dropped=0` |
| `net_client_dies` | client attaches, sends an echo request, exits with its RX buffers posted | reset before unmap; the terminal level | `client gone: device reset, ring unmapped`; machine status 0 |
| `net_wait_abandoned` | client tells `WaitEvent` (drops the claim), then calls it | abandoned obligation discarded | the second is served, not `Busy` |

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
two traits: `Registers` (read and write a 32-bit register at an offset) and
`Dma` (words of the queue memory, with the barrier). The process supplies
the volatile window, the mapped queue memory and the native fence; the tests
supply a fake device that behaves as QEMU does, then misbehaves one way at a
time. Each row is a test that goes red when its check is removed or
inverted:

- `MagicValue` wrong; `Version` 1 and 3; `DeviceID` 0 and 4;
- `VERSION_1` not offered; `MAC` not offered; `STATUS` offered and not;
  a feature outside the accepted set never written back;
- `FEATURES_OK` cleared by the device;
- `QueueReady` 1 before setup; `QueueNumMax` 0, 1, 32 and 256;
- `ConfigGeneration` changing once during the `mac` read;
- used `idx` advanced past the chains in flight; `id` odd, past the queue,
  and of a chain not in flight; RX `len` below 12 and past 12 + the slot;
  `num_buffers` 2;
- `DEVICE_NEEDS_RESET` at a configuration change;
- completions out of order: TX slots released, and RX slots published, only
  over the completed prefix;
- the order of a client's end: reset, then unmap (the fake records calls);
- the barrier between descriptor writes and `avail.idx`, and between
  `avail.idx` and `QueueNotify`; no notify when `used.flags` is 1.

`HOSTED_TESTS` gains `drivers/virtio` and `tools/netpeer`.

---

## 8. Size and units

More than one M brief: seven units. Sizes as §10 of the networking design
uses them (S days, M a week or two).

| Unit | Size | What | Files |
|---|---|---|---|
| **A3-K1** | S | `MappingOp.Physical` (§3.4), after the owner's ruling | `spec.md` §2.5; `kernel/abi/src/ops.saw`, `rights.saw`; `kernel/core/dispatch.saw`; `kernel/sysapi/src/mapping.saw`; `tools/sosimage/src/rights.saw`; `tests/mapping-physical/` and siblings (denied, husk, revoked; two rows of one allocation answer one `Physical` and, on arm64, two `Base`s); `tools/sos_runner.py` cases |
| **A3-K2** | S | arm64 second device window (§3.5) | `hal/arm64/kernel/lib.saw`, `hal/arm64/kernel/ABI.md`, `hal/arm64/user/ABI.md`; a case that maps a page of the 0x0A00_0000 block (A3-3's probe serves) |
| **A3-1** | S | `pktring` in-place accessors (§4.2); `netdevproto` doc: `rx_dropped` on a zero-copy driver, the first `WaitEvent` after `Attach`, ending the connection is releasing the outlet | `drivers/pktring/src/lib.saw`, `drivers/pktring/tests/input.saw`, `drivers/netdevproto/src/lib.saw` (doc only) |
| **A3-2** | M | `drivers/virtio`, hosted: MMIO transport v2, negotiation, split queues with the device checks, fake-device tests (§7.4) | `drivers/virtio/` (new); `tools/sos_runner.py` `HOSTED_TESTS` |
| **A3-3** | M | board rows (§2.3); `tools/netpeer` (§6.4-6.5) with hosted tests; the runner's net path (§6.3); `tests/virtio-probe`, a child that maps its window and prints `MagicValue`, `Version`, `DeviceID`, `VendorID` and the config MAC, with cases `virtio_probe` and `virtio_probe_empty` | `hal/riscv32/board.toml`, `hal/riscv32-flat/board.toml`, `hal/arm64/board.toml`; `tools/netpeer/` (new); `tools/sos_runner.py`; `tests/virtio-probe/`; `tests/images/virtio_probe*.toml` |
| **A3-4** | M | `drivers/svc-virtio-net`; `tests/net-ping`; `net_ping`, `net_no_status`, `net_legacy`, `net_wrong_device`, `net_no_device` | `drivers/svc-virtio-net/` (new, with `native/fence-riscv32.c`, `native/fence-arm64.c`); `tests/net-ping/`; `tests/images/net_*.toml`; `tools/sos_runner.py` |
| **A3-5** | S-M | `tests/net-client` and the rest of §7.3's table | `tests/net-client/`; `tests/images/net_*.toml`; `tools/sos_runner.py` |

**Order.**

1. A3-K1 waits for the owner (Q1). A3-1, A3-2 and the riscv32 and flat part
   of A3-3 need nothing and can start now, two at a time.
2. A3-K2 before A3-3's arm64 probe row.
3. A3-4 after A3-1, A3-2 and A3-3. On riscv32 and the flat profile it can
   use `base()` if A3-K1 is not in yet (§3.7), and switch to `physical()`
   when it lands; its arm64 rows wait for A3-K1 and A3-K2.
4. A3-5 after A3-4.

`tools/sos_runner.py` is touched by A3-K1, A3-2, A3-3, A3-4 and A3-5; by the
file-overlap rule they do not run at the same time. A3-3 and A3-4 are the
large runner changes; A3-K1, A3-2 and A3-5 add cases or a list entry.

---

## 9. Questions for the owner

1. **Add `MappingOp.Physical` on a new `MappingRight.Physical`, minted by
   `map`, narrowed by a launcher's ceiling (§3.4)?** Recommended: yes. It is
   the one way a driver on arm64 can give a device an address, it reveals
   only where the holder's own rows lie, and `Base` already reveals that on
   riscv32 and the flat profile.
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
5. **The driver's death ends the image (`on-exit = "shutdown"`), rule 5.1
   applied to virtio-net, until a restart unit exists (§4.2)?** Recommended:
   yes. A dead driver's queue memory goes back to its pool while the device
   still holds its addresses.
6. **A ring fault releases the driver's one outlet, which ends the
   connection for every client, and the driver then exits, ending the image
   (§5.4)?** Recommended: yes for A3, whose one client is the stack: a stack
   that corrupts its own ring is a stack bug, and netdevproto already says
   the connection ends. Revisit when A6 puts several clients behind
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

---

## 10. Spec changes proposed

| Where | Change | Unit | Question |
|---|---|---|---|
| spec.md §2.5, Mapping (l.1430-1452) | "Five ops"; `Physical` on `MappingRight.Physical` (bit 11), text in §3.4 | A3-K1 | Q1 |
| spec.md §2.5, "A region is a capability" (l.1020-1031) | names `Physical` beside `Base` and `Len` | A3-K1 | Q1 |
| spec.md §2.5, DMA paragraph (l.1558-1570) | one sentence: a DMA driver learns bus addresses from its rows | A3-K1 | Q1 |
| `hal/arm64/kernel/ABI.md`, `hal/arm64/user/ABI.md` | two device blocks, not one | A3-K2 | Q2 |
| `drivers/netdevproto` docs (not the spec) | `rx_dropped` on a zero-copy driver; the first `WaitEvent`; ending a connection | A3-1 | Q6 |

---

## 11. What was checked, how

| Fact | How |
|---|---|
| transports, addresses, lines, `dma-coherent`, edge flag on arm64 | `-M virt,dumpdtb=...` with a `virtio-net-device` on both machines, `dtc -I dtb -O dts` (`so103a3/exp1.sh`, `rv.dts`, `a64.dts`) |
| default bus placement; `force-legacy = true`; region 0x200 | HMP `info qtree` (exp1) |
| `Version` 1 by default, 2 with `force-legacy=false`; `DeviceID` 1 at the device, 0 elsewhere; `bus=virtio-mmio-bus.0` places at 0x1000_1000 | HMP `xp /4wx` on a paused machine (exp1) |
| QEMU's offered config (`mac`, `status` = 1, nothing past), `QueueNumMax` 256 | HMP `xp` at 0x10008100 and 0x10008034 with `force-legacy=false` (`so103a3/exp2.sh`) |
| `virtio-net-device` properties and defaults (`status`, `mrg_rxbuf`, `host_mtu`, `iommu_platform`, `mq`) | `-device virtio-net-device,help` |
| `-netdev stream` syntax (`inet` and `fd`), the 4-byte length framing, `connecting` at start, the NIC's link bit independent of the stream | `so103a3/exp3.py` with `filter-dump` |
| paused start, `NETDEV_STREAM_CONNECTED`, `prelaunch` then `cont`; `-object monitor-qmp` | `so103a3/exp4.py`, `exp4b.py` |
| QEMU source facts (irq level, `force-legacy` default, reset, ACK, no `VERSION_1` enforcement; RX drop of a frame that does not fit, header length, link status notify; stream `link_down` and framing) | QEMU master `hw/virtio/virtio-mmio.c`, `hw/net/virtio-net.c`, `net/stream.c`, `net/stream_data.c`, read online; line numbers approximate |
| virtio 1.2 sections | OASIS virtio-v1.2-cs01 HTML: §2.1, §2.4, §2.7, §3.1.1, §4.2.2-4.2.4, §5.1.1-5.1.6 read directly; §6 (reserved bits) not reachable in the published HTML, cited from the spec by name |
| arm64 grant and device windows, table pool | `hal/arm64/kernel/lib.saw` l.488-608, l.1085-1300, l.2060-2100 |
| kernel Mapping ops, grant row's `pa` | `kernel/core/dispatch.saw` l.3620-3760, `kernel/core/pslot.saw` l.116-119, `kernel/abi/src/ops.saw` l.809-853, `rights.saw` l.750-768, l.1264-1267 |
| builder binding of devices, pools, inputs | `tools/sosimage/src/imagefile.saw` l.1-80, l.815-1120; `declaration.saw` l.1-120; `board.saw` l.1-120, l.655-720 |
| the netstack's time use | `drivers/netstack/src/lib.saw` l.20-27, l.287-334, l.421-470, l.690-698; `arp.saw` l.99-226 |

Each experiment ran under the sawos suite lock, taken with `mkdir` and given
back with `rm owner; rmdir`.
