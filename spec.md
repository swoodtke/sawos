# SOS — a capability-based microkernel in Saw

Working notes. Name "SOS" is a placeholder (Saw OS). Requirements in
§1 are ratified (user, Jul 29); later sections are proposals and open
questions for discussion. First target: ESP32-P4 (riscv32) via Saw's
freestanding profile; the design must not preclude MMU-class targets.

## 1. Ratified requirements (user)

- **Capability-based microkernel.** The kernel implements only what is
  required to efficiently provide core OS resources:
  processes/address spaces, threads/tasks/scheduling, timers,
  interrupts, memory management. Everything else runs in userspace,
  with processes communicating via shared memory and channels/events.
- **Handles.** Every kernel resource is referenced by a Handle: an
  opaque integer mapping to a kernel object through a per-process
  table lookup (fd-style). No ambient authority.
- **Derivation.** A Handle is ideally obtained via an operation on
  another Handle the process already holds, with the required
  capability enabled — authority flows only through held authority.
- **Channels** send data synchronously, optionally carrying Handles,
  which are MOVED to the receiving process. Not every Handle is
  movable — transferability is a capability of the Handle itself.

## 2. Proposed object model (discussion)

Minimal kernel object types (each a slab-allocated kernel struct;
names provisional):

| Object | Role |
|---|---|
| `AddressSpace` | Isolation domain, defined abstractly. P4: PMP region set + APM/REE security context (see §5.5 — the P4's MMU is real but global/external-memory-only, not per-process). Paging targets: page-table root. |
| `Thread` | Kernel-scheduled execution context bound to an AddressSpace. Saw's cooperative TaskGroups run *inside* a thread, in userspace — the kernel never sees tasks. |
| `MemoryObject` | A range of memory (RAM or device MMIO) that can be mapped into AddressSpaces. Derived by splitting/attenuating a parent MemoryObject; roots handed to the first process at boot. |
| `Channel` | Synchronous message IPC with request/reply built in — see §2.1 (ratified Jul 29). |
| `Event` | Accumulating non-blocking notification (OR / saturating-sum); a waitable — see §2.4 (ratified Jul 29). |
| `Timer` | Deadline object; fires an Event / is waitable. |
| `Interrupt` | Binds an IRQ line to a waitable; userspace drivers wait on it, ack via the handle. |
| `Waiter` | Generic wait aggregator (epoll/Port-style) — see §2.2 (ratified Jul 29). |
| `MemoryObject` | Physical memory (RAM or device MMIO). Ownership/authority over the pages; mappable, sendable — see §2.3 (ratified Jul 29). |
| `Mapping` | An installed virtual placement of a MemoryObject; distinct object, own handle; only it can unmap — see §2.3. |
| `Process` | AddressSpace + handle table + threads (ratified Jul 29: NO kernel Job/hierarchy). Kernel guarantees teardown on exit/fault — closing all handles, freeing/unmapping owned memory. Supervision (restart, kill-trees, launchd-style) is a USERSPACE concern. |

### 2.1 Channels: bounded messages + built-in request/reply (ratified Jul 29)

- **`send(data?, handles?) -> ReplyHandle`.** Body bytes are copied
  from the sender's address space; handles are attached but TRANSFERRED
  into the receiver's table only when the receiver actually receives
  the message (rendezvous transfer — no orphaned handles if the send
  is abandoned). Both are OPTIONAL (data-only, handles-only, or both).
- **Bounded by design (kept simple on purpose):** a fixed maximum body
  size and a fixed maximum handle count per message → the kernel
  stages one message in a fixed slot, zero dynamic kernel allocation.
  Bulk data goes through shared memory (§2.3); the small message
  carries the MemoryObject handle. (Concrete limits: TBD — a small
  body, e.g. 64–256 bytes, and a handful of handles.)
- **Request/reply is a PRIMITIVE, not a convention.** `send` returns a
  **ReplyHandle** — a single-use, one-shot "reply channel" the sender
  waits on. `receive(...) -> (message, RequestHandle)` returns a
  matching one-shot **RequestHandle** the server replies through
  (`request.reply(data?, handles?)`). Properties:
  - The reply capability is unforgeable and **consumed on reply**
    (NoCopy in Saw — the type system enforces single-use on top of the
    kernel's own check).
  - If the server drops its RequestHandle without replying, the
    client's wait wakes with a **peer-closed error** — no hung
    clients, no timeout hacks. (seL4 call/reply + Zircon channel-call,
    fused.)
  - ReplyHandle/RequestHandle **are transferable** (ratified Jul 29) —
    this is the delegation primitive, not a hazard. A server may MOVE
    its RequestHandle to a third party who replies directly to the
    original client. **Canonical example — zero-copy delegation:** the
    filesystem receives a read (a RequestHandle), forwards that handle
    over a channel to the flash driver; the flash driver replies
    through it with the hardware bytes straight to the client — the
    payload never transits the fs address space. Single-use is
    preserved through transfer: the handle is consumed on reply
    regardless of who holds it (transfer moves the reply *obligation*).
  - **Fire-and-forget is NOT a channel concern** — use an Event
    (§2.4), the accumulating non-blocking notification object.

### 2.2 Waiter: the generic wait aggregator (ratified Jul 29)

- epoll/Port-style. `waiter.add(handle, key)` attaches a waitable;
  `waiter.wait() -> (key, readiness)` blocks until any attached handle
  is ready and returns the **word-sized key the attacher supplied**
  (not just a bit — a word can directly encode the waiting Saw task's
  identity, so kernel wake sources dispatch to userspace tasks with no
  side-table lookup — the async-executor integration point).
- Waitables: Channel (readable / reply-ready), Event, Timer,
  Interrupt, ReplyHandle. **Attach semantics (ratified Jul 29):**
  **level-triggered** (keeps reporting ready until the waiter handles
  it — no lost edges); **persistent** attachments with explicit
  `remove(handle)` (no one-shot mode); a handle attaches to **at most
  one Waiter**, but **multiple threads/tasks may wait on the same
  Waiter** (a ready handle wakes one waiter — the shared
  readiness-queue shape).

### 2.4 Event: accumulating non-blocking notifications (ratified Jul 29)

- The fire-and-forget primitive. `event.signal(bits)` NEVER blocks;
  the kernel accumulates into the Event's word. `event.receive() ->
  word` drains and resets to 0. Waitable (non-zero = ready → attach to
  a Waiter).
- **Accumulation mode is a per-Event property:**
  - **bitwise-OR** — "which of these events occurred since last drain"
    (flag set; the signal use case).
  - **saturating-sum** — "how many times" (counting-semaphore use case;
    SATURATES rather than wrapping — a fast producer must not corrupt
    the count).
- Lowers to the kernel's atomic fetch-or / saturating-fetch-add on the
  Event word (the design-41 Atomic machinery). Multiple senders race
  harmlessly (atomic accumulate); one drain reads-and-clears.

### 2.5 Memory: MemoryObject vs Mapping (ratified Jul 29)

- **`MemoryObject`** = authority over a physical page range, allocated
  **from a typed pool** (ratified Jul 29). The pool's attribute governs
  three things at once:
  - **RAM pool** (cacheable, `Normal`): unique allocation — a region
    is allocated once and unavailable until freed; returns to the pool
    when its last reference drops (see refcount below). Maps as
    `UnsafeMemory<_, Normal>`-class cacheable memory.
  - **Device pool** (uncacheable MMIO, `Device`): a fixed region may be
    handed out many times (multiple drivers can hold handles to the
    same peripheral window) and is **never freed** — MMIO is pinned.
    Maps as `UnsafeMemory<_, Device>` volatile memory.
  - The pool attribute IS design 46's intent marker surfacing at the
    kernel layer — a Device MemoryObject can only produce Device
    mappings, so a driver physically cannot get a cached view of a
    register block. The attribute travels with the handle.
- Sendable over channels — so the SAME physical memory can be mapped
  into multiple address spaces at multiple virtual locations (the
  shared-memory primitive). Derived by splitting / attenuating a
  parent; pool roots given to the root server at boot.
- **`map(aspace, ...) -> Mapping`.** Mapping is a DISTINCT kernel
  object with its own handle, recording the virtual placement. **Only
  the Mapping handle can unmap** (`mapping.unmap()` / its Deinit).
- **Deliberate stance:** dropping a Mapping handle without unmapping
  means that mapping is permanent — safe-but-leaked, never a
  dangling-unmap hazard. In Saw, a Mapping's Deinit unmaps at scope
  exit, so the common path is leak-free by construction.
- **Refcount invariant:** a Mapping holds a reference to the physical
  pages (not merely to a MemoryObject handle). The pages are freed
  only when the LAST reference of either kind — any MemoryObject
  handle OR any Mapping — drops. This is what makes multi-mapping
  safe: no mapping can outlive its backing.

## 3. Handles, rights, derivation (proposal detail)

- Per-process handle table: index → (object ref, rights word).
  Handles are plain integers in the syscall ABI; the kernel validates
  index + generation (stale-handle detection) + rights on every use.
- **Rights are a bitmask**, e.g.: READ, WRITE, MAP, SIGNAL, WAIT,
  MANAGE (derive children), **TRANSFER** (may be sent over a channel —
  the movability capability). **No DUPLICATE right — no handle
  duplication at all** (ratified Jul 29): every handle is unique, the
  exact `NoCopy` correspondence. If a second handle to a resource is
  legitimately needed, the resource's CREATOR (who holds MANAGE)
  mints a fresh one; there is no in-process copy. Attenuation happens
  only at that creation, monotonically.
- **Attenuation is monotonic**: any derivation or duplication may only
  strip rights, never add. The only rights source is the boot handle
  set given to the root server.
- Handle close is explicit in ABI, automatic in Saw (Deinit).
- Syscall ABI sketch (riscv32 `ecall`, args in registers): every call
  is `(handle, op, args...) -> Result`. The kernel's dispatch is a
  table lookup + rights check + object-op — the fast path must stay
  tens of instructions.

## 4. The Saw synergy (why this language, this kernel)

- **Handle lifecycle = ownership.** Userspace handles are
  `NoCopy + Deinit` wrappers: scope exit closes (capability leaks are
  a compile error class); `move` is transfer;
  `channel.send(move h)` enforces at COMPILE TIME that the sender no
  longer holds what it sent. Kernel-side transferability (TRANSFER
  right) is checked at runtime; the language makes the userspace
  discipline free. Distinct types (`type TimerHandle = ...`) make
  handle-kind confusion unrepresentable in the syscall wrappers.
- **`sync` effect = ISR discipline.** Interrupt entry paths and
  kernel critical sections are `sync` contexts — suspension-free,
  compiler-verified.
- **Slabs + statics (designs 41/42)**: kernel objects live in
  fixed-size per-type slabs over static regions — allocation-free
  after boot, exhaustion is an explicit Err, the D4 model verbatim.
- **UnsafeMemory (design 46)**: userspace drivers get MMIO
  MemoryObjects mapped into their space and wrap them in
  `UnsafeMemory<Regs, Device>` register blocks — typed, volatile,
  access-mode-checked driver code with no kernel driver surface.
- **Cooperative tasks in userspace**: the kernel schedules THREADS
  only; Saw TaskGroups/channels give each process its own async
  runtime; kernel Events integrate as wake sources (the __wake
  protocol's externally-signaled variant — future design).
- **No-forced-destroy carries over**: thread/process teardown is
  cooperative-first (signal + join); the kill-tree question is §5.3.

## 5. Open questions (need decisions before the kernel brief)

1. ~~Channel semantics~~ RESOLVED §2.1: bounded messages (max body +
   max handles), rendezvous handle transfer, built-in request/reply
   via ReplyHandle/RequestHandle. Remaining sub-questions: the
   concrete size/count limits; and whether the base send is
   rendezvous (sender blocks for a receiver) or the reply-object model
   makes it naturally call-style (sender blocks on the reply, so the
   send itself can deposit-and-return once staged) — lean call-style,
   confirm.
2. ~~DUPLICATE right~~ RESOLVED §3: no duplication; unique handles;
   creator re-mints when a second is needed. (Mirrors NoCopy exactly.)
3. ~~Process/Job hierarchy~~ RESOLVED §2 table: flat Process, kernel
   teardown-on-exit/fault only, supervision in userspace.
4. ~~The async wait primitive~~ RESOLVED §2.2: the `Waiter`
   aggregator with word-sized keys.
5. **P4 memory-hardware realities (verified against Espressif docs,
   Jul 29):** The P4 HAS an MMU with basic virtual memory — but it
   translates EXTERNAL memory only (SPI flash + PSRAM), as a single
   GLOBAL mapping (one system-wide virtual pool, per-page R/W/X, no
   ASIDs); internal SRAM is physically addressed and bypasses it. The
   cores implement Machine + User modes only — no S-mode, hence no
   Sv32 per-process paging. Isolation = up to 16 PMP/PMA regions
   (first-level check) + the APM/TEE module (bus-level permission
   contexts; User-mode accesses tagged into one of 3 REE contexts).
   Kernel consequences: AddressSpace on P4 = PMP region set + APM/REE
   context; the global MMU mapping is a KERNEL-managed resource
   (executable code / large data mapped from flash/PSRAM with W^X —
   shared mapping, not per-process; per-context remapping would cost
   cache flushes). Physical memory, no overcommit/COW, small-N
   processes (PMP/REE context counts bound simultaneous domains).
   Document as a profile, not a limitation of the model.
7. ~~Waiter attach semantics~~ RESOLVED §2.2: level-triggered,
   persistent + remove, one-Waiter-per-handle, many-waiters-per-Waiter.
8. ~~send_oneway~~ RESOLVED §2.4: replaced by the accumulating Event.
9. ~~Memory RAM/Device distinction~~ RESOLVED §2.5 (typed pools). The
   remaining sub-detail: exactly where the per-physical-region refcount
   lives (a pool-side region descriptor) and reconciling it with
   Device regions that are never freed (their count is effectively
   pinned / ignored for reclamation). Implementation detail for the
   memory brief.
6. **Boot protocol**: kernel hands the root server a boot channel +
   root MemoryObjects (RAM, device ranges) + root IRQ table handle.
   Everything else derives. Shape of the boot image (kernel + root
   server linked together vs loaded) — with Blade as the build tool.
7. **Syscall ABI details**: register convention, error model
   (Result-shaped: negative errno vs (status, value) pair), and the
   Saw syscall-wrapper layer (a `sos` userspace crate/module — the
   typed Handle wrappers live there, not in the kernel).

## 6. Explicitly NOT in the kernel

Drivers (userspace via Interrupt + MMIO MemoryObjects), filesystems,
network stacks, POSIX personality, dynamic linking/loading, package/
process management policy, time-of-day (a userspace service over the
Timer primitive). The kernel knows objects, handles, rights, threads,
memory words, and nothing else.
