# SO-150: a received Memory's length (design note)

## 1. The problem, as the code has it

A pktring client allocates the ring's Memory and gives it at `Attach`
(`drivers/netdevproto/src/lib.saw`, `drivers/hciproto/src/lib.saw`,
`drivers/sockproto/src/lib.saw`). The server maps it and makes a
`SharedWindow` of `Layout.bytes()` over the row's base
(`drivers/pktring/src/lib.saw`, `SharedWindow.init(at:bytes:)`). Nothing tells
the server how many bytes the row it installed actually grants; the module doc
says so ("A server cannot ask the kernel how long a Memory it was given is, so
it maps the ring trusting the client to have allocated `Layout.bytes()`").

`SharedWindow.word` checks an offset against the window's `bytes`, which is
the layout's length and not the row's. So a client that gives a Memory shorter
than the layout gets past every check pktring makes, and then:

- on riscv32 and arm64 the server's first access past the row is a hardware
  fault, and the server dies;
- on riscv32-flat and the ESP32-S3 (the networking target) nothing faults: the
  server reads and writes the bytes after the client's allocation, which are
  another allocation's, a pool's free bytes, or the kernel's.

Both break rule 5.2.4 of the networking design (a bad value ends the
connection with a counted error, never a fault of the receiver), and the flat
case is worse than a fault.

The kernel has the number. `install_row` (`kernel/core/dispatch.saw`) records
every row as a `GrantRow` with `base` and `top` (`kernel/core/pslot.saw`), and
`MemoryOp.Map` installs a row over exactly the allocation (`memory_op_decoded`,
`top = base + len`). `MappingOp.Base` already reads that record
(`mapping_op_decoded`, the `Base` arm answers `grants[row].base`). No op reads
`grants[row].top`.

## 2. Recommendation

**Add `MappingOp.Len`: how many bytes a Mapping's row grants, spending
`MappingRight.Base`.** The server maps the received Memory, asks the Mapping
its length, and refuses a row shorter than `Layout.bytes()` before it touches
a byte. pktring gets a checked window constructor that takes the row's base and
length and answers `RingError.ShortMemory` for a short one, counted like every
other peer value that fails a check.

Why the Mapping and not the Memory:

1. **It answers the question the fault asks.** An access is held to the row,
   not to the allocation. The two agree when a row is made, and they part when
   an allocation is extended and its row is not grown (`MemoryOp.Extend` keeps
   every row's bounds; spec §2.5). A check against the allocation would pass
   bytes the row does not grant. A received Memory cannot be extended (only a
   `Front` allocation carries `Extend`, and it carries neither `Transfer` nor
   `Mint`), so for pktring the two numbers are equal; but the op that answers
   the row is right everywhere, and the op that answers the allocation is
   right only there.
2. **It keeps §2.5's stance where it matters.** "There is no op that reads a
   pool's or an allocation's bounds" stays true of the two kinds it is about.
   A Mapping already answers where its row is (`Base`); its length is the other
   half of the same fact, about a domain its holder already had the authority
   to edit. No new right: locating a row is knowing where it begins and where
   it ends, so `Len` spends `Base`, and every keep mask written to let a holder
   locate a row (the ordinary "find, don't revoke" shape, and the runtime's
   `MAPPING_SPENT` in `kernel/sysapi/src/rt.saw`) lets it read the length with
   no audit.
3. **It costs nothing in the ABI.** One op number, no argument, one value
   word, the refusals `Base` already has.

The check comes after the map and before the first access, which is what rule
5.2.4 needs: installing a row reads and writes nothing. The issue's title
asks for the check before the map; nothing is lost by moving it one op later,
because what a map of a short Memory costs the server is a Mapping slot and a
quota row for the length of one refusal (it unmaps and releases at once).

## 3. The alternatives

**(a) `MemoryOp.Len` on the allocation** (`MemoryOp` 2, answering
`MEMORIES[slot].len`). It lets the check precede the map. Against it:

- it answers the allocation, not the row (point 1 above);
- it reverses §2.5's stance on the kind itself, and every site that leans on
  it changes with it (`kernel/sysapi/src/memory.saw`'s "There is no way to read
  the bounds", the `MemoryOp` doc in `kernel/abi/src/ops.saw`, and by analogy
  `kernel/sysapi/src/iomemory.saw`, where the same pressure would follow);
- its gate is bad either way. A new `MemoryRight.Len` bit fails closed under
  every keep mask written before it, so a launcher's `Map | Transfer` mask
  would leave a server unable to check the ring it maps, and `memory_rights()`
  and `pool_rights()` grow a bit. Ungated, it is the only ungated op on an
  object (`INFO_OP` is ungated because it describes the handle, not the
  object), and it hands the length to a holder without `Map`, which can do
  nothing with it.

A `Stats`-style record (`MemoryOp.Info`: length, granularity, kind of
origin) is (a) with a copy-out funnel and more to leak; rejected for the same
reasons.

**(b) `Map` taking a minimum length.** Its three argument registers are full
(target, access, room; spec §5.7: "An op that needs more than three argument
words passes an argument record in memory"). So it is an argument record on
the most common memory op, or a second op that installs a row (two doors to
one act), or a length packed into the access word, which on Profile A collides
with the access bits (the grain is four bytes). The refusal must be a status,
since the caller cannot check it, and needs a name (`NoMemory` is the nearest).
Every other map caller pays for the wider ABI, and the receiver still never
learns the length, so a server whose layout follows the memory it is given
cannot use it.

**(c) Anything already readable.** Nothing on the user side: `INFO_OP`
answers the handle's kind and rights, `Base` the row's start, and
`Pool.available` a pool's free bytes. `MappingOp.Grow` answers `NoMemory` past
the allocation's end, but a row covers its whole allocation when it is made,
so probing with `Grow` learns nothing and grows the row when it succeeds. The
number is in the kernel's `GrantRow`, which is where this design reads it.

**(d) The server allocates the ring.** It would know the length. It inverts
creator-pays: the server's pool would pay for every client's ring, and a
client could exhaust it by attaching. The networking design chose client
allocation for that reason; kept.

**(e) A length in the `Attach` message.** It is the client's claim, the value
being checked; worthless.

**A record answering base and length together** (one op, copy-out) changes
`Base`'s shape and its callers (`rt.saw`'s `first_growth`) for a two-word
answer two one-word ops give with no funnel. Rejected.

## 4. What a length leaks

What `Len` reveals is the row's length, which when the row is made is its
allocation's. Who learns it: the holder of a Mapping with `Base`. A Mapping is
minted into the table of the process that called `map` (`install_row`'s
`mint_capped(p, ...)`), it is not givable (`mapping_rights()` withholds
`Transfer`), and making it took `MemoryRight.Map` on the allocation and
`ProcessRight.Map` on the target.

Is a Memory's length a secret anywhere? No place in the tree treats it as one:

- **The sender chose it.** A receiver learns the length of what it was given,
  which the giver could have written in the message.
- **Nothing about the pool.** An allocation's length says nothing about the
  pool it came from beyond itself. A pool's free state is `Pool.available`'s
  and stays behind `PoolRight.Allocate`.
- **Addresses are not secret either.** On the identity tiers `Base` already
  answers the physical base, so `Base` and `Len` together are the
  allocation's physical range there. Protection on every tier rests on rows
  and handles, never on a process not knowing a number; the build's region
  table and linker scripts are fixed per image, and arm64 placement is
  deterministic (`tests/map-placed` pins it).
- **No channel.** A transferable allocation's length never changes (only a
  `Front` allocation extends, and it never leaves its process), and a row's
  length changes only by its own holder's `Grow`. So `Len` carries nothing
  from another process that a message could not.

What the stance sentences claim does change, and section 6 rewrites them: the
honest statement is that no op on a pool or an allocation reads its bounds,
and that a holder learns its own rows' bounds from the Mapping.

## 5. Every profile

The arm is core code (`kernel/core/dispatch.saw`) over the grant record,
which every HAL keeps; no HAL changes.

- **riscv32** (PMP, identity, a four-byte grain): the row is the
  allocation's own range and `Len` its length. Without the check, a PMP
  fault ends the server.
- **arm64** (translating, a page grain): the row is a placed range of user
  addresses with the allocation's span, and `Len` that span. Without the
  check, a data abort ends the server.
- **riscv32-flat and the ESP32-S3** (Flat): the row is recorded and not
  enforced, and `Len` reads the recorded span. Without the check, the server
  writes past the ring into whatever lies after it.

On the Flat tier `Grow` moves the record's top (spec §2.5), so `Len` reads the
same field it reads elsewhere. The ESP32-C3 builds nothing today and needs
nothing.

## 6. The spec text it changes

**§2 (the kinds table, `Mapping` row).** Now:

> One installed protection row, with its own handle. Ops
> `Unmap`/`Base`/`Grow`; rights `MappingRight.Unmap`/`.Base`/`.Grow`. Not
> givable. §2.5.

Becomes:

> One installed protection row, with its own handle. Ops
> `Unmap`/`Base`/`Len`/`Grow`; rights `MappingRight.Unmap`/`.Base`/`.Grow`.
> Not givable. §2.5.

**§2.5, "A region is a capability, not a description."** Now:

> There is no op that reads a pool's or an allocation's bounds: a process
> that holds one can hand it to the kernel and cannot learn a number from it.
> What a process knows about where its memory is, it knows from the config
> that gave it the region — which is what root's tag-to-meaning config is,
> and where a driver's `UART_BASE` comes from.

Becomes:

> There is no op on a pool or an allocation that reads its bounds: a process
> that holds one hands it to the kernel, and what it gets back is another
> capability. The numbers a holder learns are about its own rows, once it has
> mapped: a `Mapping` answers where its row is and how long it is (`Base`,
> `Len`, below), which a program needs to use the memory and a receiver needs
> to check memory it was given before it touches it. Where a row's address is
> its memory's own, those two are the allocation's physical range. What a
> process knows about where its memory is before it maps, it knows from the
> config that gave it the region — which is what root's tag-to-meaning config
> is, and where a driver's `UART_BASE` comes from.

**§2.5, `Mapping`, the "Three ops." bullet.** Now it opens:

> **Three ops.** `Unmap` (on `MappingRight.Unmap`) and `Base` (on
> `MappingRight.Base`) — the address the kernel chose, asked for rather than
> assumed, ...

Becomes "**Four ops.**", and after the sentence ending "on a tier that does
not translate it is also the physical one." insert:

> `Len` (also on `MappingRight.Base`) answers how many bytes the row grants
> from its base: the row's top less its base, read from the grant record, a
> whole number of grains on every row. Locating a row is knowing where it
> begins and where it ends, so it spends the right `Base` spends. It answers
> the row and not the allocation, because the row is what an access is held
> to: a `Grow` raises it, and an `extend` no `Grow` has followed does not. A
> row covers its whole allocation when it is made, so this is how a process
> given memory checks its length before it touches a byte: a shared ring
> shorter than its layout is a peer's bad value, refused by the receiver's
> protocol, never a fault of the receiver. It answers for every row a Mapping
> names, a device window's and the stats region's included. Its answers are
> `Base`'s, in `Base`'s order: a handle without `Base` is the `AccessDenied`
> fault, a Mapping its holder unmapped the `BadState` fault, and one whose
> target died `Revoked`.

In the same bullet, `Grow`'s "a status because the holder of a Mapping need
not hold the `Memory` and cannot read its bounds" becomes "a status because
the holder of a Mapping need not hold the `Memory` and cannot read the
allocation's bounds (`Len` answers the row's)".

**§2.5, "It is bound to its target."** "so `Unmap`, `Base` and `Grow` answer
`Revoked`" becomes "so `Unmap`, `Base`, `Len` and `Grow` answer `Revoked`".

**§5.6, a destination's refusals.** "because a launcher cannot read a
region's bounds (§2.5)" becomes "because no op on a region reads its bounds
(§2.5)". (A launcher could map a destination into itself and ask; one that
does not still has the status.)

**§12, the stitcher's rows.** "it would need the blob's length, which it
cannot read (§2.5)" becomes "it would need the blob's length before it holds
the blob (§2.5)".

## 7. The ABI

- **Op:** `MappingOp.Len = 3`, appended after `Grow = 2`
  (`kernel/abi/src/ops.saw`). The table's `< PANIC_OP` assert moves from
  `Grow` to `Len`. The `MappingOp` doc's "Three ops." becomes "Four ops."
  with a sentence for `Len`, and the case gets a doc in `Base`'s style.
- **Registers:** the Mapping handle and the op; no argument (the wrapper
  passes zeros, as `base()` does). The answer is the length in the value
  register (`a1`/`x1`); a row lies in the address space, so a word holds it.
- **Answers, in order** (exactly `Base`'s):
  1. a handle without `MappingRight.Base`: `AccessDenied` fault;
  2. a freed slot: `BadState` fault;
  3. a husk whose target died (`ROW_TARGET_DIED`): `Revoked` status;
  4. a husk its holder unmapped (`NO_ROW`): `BadState` fault;
  5. `Ok`, value `grants[row].top - grants[row].base`.
- **Rights:** none new. `MappingRight.Base`'s doc in
  `kernel/abi/src/rights.saw` becomes "May this handle ask where the row is
  and how long (`MappingOp.Base`, `MappingOp.Len`)?"; `mapping_rights()` is
  unchanged.
- **Kernel arm** (`mapping_op_decoded`, beside `Base`): `Base`'s prologue,
  then `op_value(g.top - g.base)` for `g = PROCESSES[body_of(t)].grants[row]`.
  The prologue is now shared by two arms; a helper that faults or answers the
  row (or `ROW_TARGET_DIED`) is the implementer's call.
- **No floor export.** Like `Base`, it is reached through `call_value` and has
  no `@export`ed `sos_mapping_len`, so no image grows; the comment in
  `kernel/sysapi/src/floor.saw` ("There is no `sos_mapping_base` ...") names
  both ops.

## 8. The sysapi wrapper

In `kernel/sysapi/src/mapping.saw`, after `base()`:

```saw
    /// How many bytes this mapping grants, from `base()`. Requires
    /// `MappingRight.Base`, as `base()` does: where a row begins and where
    /// it ends are one authority.
    ///
    /// It answers the row, which is what an access is held to: `grow`
    /// raises it, and a `Memory.extend` that no `grow` has followed does
    /// not. A row covers its whole allocation when it is made, so a process
    /// given memory learns its length here once it has mapped it, and can
    /// check it before it touches a byte.
    ///
    /// The refusals are `base()`'s: an unmapped Mapping ends the caller with
    /// `BadState`, and one whose target has died answers
    /// `Err(SosStatus.Revoked)`.
    public func len(&self) -> Result<UInt, SosStatus> {
        call_value(self.handle, MappingOp.Len as UInt, 0, 0, 0)
    }
```

The module doc's "`base()` reads the address that row was placed at" gains
"and `len()` how long it is". `Memory`'s doc ("There is no way to read the
bounds. What a caller names is a target (`map`), and what it gets back is
another capability.") gains "whose `len()` says how many bytes its row
grants".

## 9. The pktring change

pktring stays a hosted library with no dependency; the kernel number enters
through the window's constructor.

- `RingError.ShortMemory(len: UInt)`: "a memory shorter than the ring", with
  the length after it in `format`. Fatal (`is_fatal`'s catch-all already
  says so).
- `FaultCounts.short_memory: UInt32`, recorded by `record` and summed by
  `total`.
- A checked constructor beside `init(at:bytes:)`:

  ```saw
      /// The window over a ring laid out as `layout` in a row the kernel
      /// says is `len` bytes from `at`: a mapping's `base()` and `len()`.
      /// A row shorter than `layout.bytes()` is the peer's value, refused
      /// before anything reads it. The window is the layout's length, not
      /// the row's, so nothing past the ring is reachable through it.
      public init(at: UnsafePointer<UInt32>, len: UInt,
                  layout: &Layout) unsafe -> Result<SharedWindow, RingError> {
          if len < layout.bytes() {
              return RingError.ShortMemory(len)
          }
          return SharedWindow(base: at as UInt, bytes: layout.bytes())
      }
  ```

  It stays `unsafe`: the caller still vouches that `at` and `len` are a
  read-write row's.
- The module doc's paragraph "A server cannot ask the kernel how long ..."
  becomes the server's attach recipe: check the received handle's rights
  hold `MemoryRight.Map` (a Memory given without it would make `map` fault
  the server, and `rights()` can tell); map it read-write into itself; make
  the window from the Mapping's `base()` and `len()`; on `ShortMemory` unmap,
  release, count and refuse. The rule list in "A shared ring is input" gains
  "0. the memory is checked to hold the layout before any field is read".
- netdevproto and hciproto: their `Attach` status text ("an `Attach` with no
  Memory") becomes "an `Attach` with no Memory, one without `Map`, or one
  shorter than the ring", and the Failure paragraph says the last two are
  counted in `ring_faults`. sockproto's `BadArg` list gains "a ring shorter
  than its layout".

A server that releases its Memory handle after the map loses nothing: the row
holds the bytes (the refcount invariant), and a handle row comes back.

## 10. Tests

Each check, the case that holds it, and what goes red.

**Kernel, `Len`'s answers.**

1. *The value is the row's span.* New case `mapping_len` (root package
   `tests/mapping-len`, `pool`): a two-page `Front` allocation mapped with a
   page of room prints `len: 8192`; extended by a page and not grown, `len:
   8192`; grown by a page, `len: 12288`; unmapped, nothing more asked. A page
   is a whole number of grains on every profile, so the lines are the same on
   all four. Red: an arm answering the allocation (the second line reads
   12288), the base, zero, or the top.
2. *The right.* `child-grow-leg` gains `ROLE_LEN_UNGRANTED` (a Mapping minted
   with every right but `Base`, then `len()`); `grow_faults` prints "len
   through a mapping without Base: status=131075". Red when the check is
   removed (the child survives: 65544) or inverted (every `len()` in case 1
   faults).
3. *The holder's husk.* `child-grow-leg` gains `ROLE_LEN_HUSK` (map, unmap,
   `len()`); `grow_faults` prints "len of an unmapped mapping:
   status=131077". Red when the `NO_ROW` test is removed: the arm indexes
   row -1 and the kernel stops on an index out of range.
4. *The target's death.* `mapping_grow_gone` gains, before its `grow` line,
   "SOS growgone: len: what this call was waiting for is gone". Red when the
   `ROW_TARGET_DIED` test is removed: the arm indexes row -2 and the kernel
   stops, as in case 3.

The freed-slot `BadState` arm cannot be reached through a live handle (a
handle that names the slot holds a reference), as for `Base`; it is kept for
`Base`'s reason and has no case.

**pktring, hosted** (`drivers/pktring/tests/input.saw`, run by the full gate's
`HOSTED_TESTS`), over the existing fake memory with a layout of two 2048-byte
slots each way (`bytes()` is 8256):

5. a row one word short (8252) and a row of zero: `Err(ShortMemory(len))`.
   Red when the check is removed.
6. a row of exactly 8256: `Ok`, the window holds offset 8252 and not 8256.
   Red when the comparison is inverted to `<=`.
7. a row of 12288: `Ok`, and the window still does not hold 8256. Red when
   the window is made the row's length.
8. `FaultCounts.record(ShortMemory)` counts `short_memory` and `total`, and
   `is_fatal` is true. Red when the record arm is dropped.

**End to end, the check on a real kernel.** New case `ring_short` (root
`tests/ring-short`, child `tests/child-ring-server`, `pool`), with pktring and
the kernel together: root is the client, lays out the same layout, and gives
the child two Memories in turn, one page (4096) and three pages (12288). The
child maps each, makes the window from `base()` and `len()`, and prints:

- "short ring: refused: a memory shorter than the ring: 4096, counted 1";
- "ring accepted: the last word holds its mark", after storing a mark at
  offset 8252 and loading it back; root then reads the same mark through its
  own mapping and prints it.

Red when pktring's check is removed: on riscv32 and arm64 the child faults on
its first slot access, and on riscv32-flat and the S3 it prints "accepted"
for the short ring. Red when `Len` answers anything but the row (a zero or a
base refuses the long ring).

The two new cases join the S3 smoke's process list in
`tools/sos_runner.py` (beside `grow_faults` and `mapping_grow_gone`), and the
new packages carry a `[sos.xtensa-esp-unknown-elf]` section. None asserts a
hardware denial, so the flat profile excludes none of them, and none depends
on time.

## 11. Size of the unit

One unit, small: about 550 lines over some fifteen files, two thirds of them
tests. The kernel is one op case with its doc, one arm of about twenty lines,
a rights doc line and an assert moved; sysapi one wrapper; the spec about
thirty lines; pktring about forty lines and fifty of hosted tests; three
protocol docs a line each; four new test packages (two small roots, a child,
two roles added to `child-grow-leg`) and three runner cases. The kernel
grows by one dispatch arm on every profile (the image report prints the
sizes). The spec text is given above verbatim, so it is a Sonnet unit.

## 12. Questions for the owner

1. **The check after the map, not before it.** The issue asks for a check
   before the server maps. This design checks after the map and before the
   first access, through the Mapping (section 2). Recommendation: accept it;
   the map touches nothing, and the Mapping is the object whose length the
   access is held to.
2. **`map`'s other refusals of a received Memory.** A Memory without
   `MemoryRight.Map` makes the receiver's `map` fault, which the receiver
   can check first (`rights()`, section 9). Two cannot be checked: an
   allocation off the grain (a boot allocation) and one in flash (the S3's
   blob rows, and pools typed over them) are `BadArg` faults at `map`, and a
   client that was given one can pass it on. `ProcessCreate` answers the same
   shapes in a destination with the `BadImage` status "because a launcher
   cannot read a region's bounds". Recommendation: a follow-up issue making
   `map`'s region-shaped refusals a status, separately from this unit; no
   client on the gate or in the S3 images holds such a Memory today.
3. **A short ring at `Attach`: refused or ended.** Recommendation: answer
   `BadArg`, count it in `ring_faults`, and end the connection, as rule 5.2.4
   says for a peer's bad value; unlike "an `Attach` with no Memory", which is
   a malformed request and leaves the connection unattached.
