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
| `Interrupt` | Binds an IRQ line to a waitable; userspace drivers wait on it, ack via the handle. BUILT M2 (design 178 unit 4): one op (`Ack`), two rights (`InterruptRight.Wait`/`.Ack`), created by `ProcessOp.BindInterrupt` on its own Process right — the factory bit a launcher strips from everything that is not a driver. The BINDING IS THE OBJECT'S EXISTENCE (creation takes the line, there is no rebind), which is what stops one handle naming two devices over its life. A line the board does not have, the TIMER's line, and a line already bound are all faults. |
| `Waiter` | Generic wait aggregator (epoll/Port-style) — see §2.2 (ratified Jul 29). |
| `MemoryObject` | Physical memory (RAM or device MMIO). Ownership/authority over the pages; mappable, sendable — see §2.3 (ratified Jul 29). |
| `Mapping` | An installed virtual placement of a MemoryObject; distinct object, own handle; only it can unmap — see §2.3. |
| `Process` | AddressSpace + handle table + threads (ratified Jul 29: NO kernel Job/hierarchy). Kernel guarantees teardown on exit/fault — closing all handles, freeing/unmapping owned memory. Supervision (restart, kill-trees, launchd-style) is a USERSPACE concern. |
| `System` | Kernel singleton (ratified Aug 5): the object behind system-scoped primitives so that EVERY syscall is an object op (§5.7) — v1 ops `debug_print`, `shutdown(status)` (stop the machine; QEMU: sifive_test), rights-gated (`SystemRight.Debug`/`.Shutdown`, §3 scoped rights). Root receives its handle at boot (§12). `exit` is NOT here — process exit belongs to the Process object when it exists (ratified Aug 5). Later candidates: info queries. |

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
  `waiter.wait(buffer, capacity)` blocks until any attached handle is
  ready and **copies out a RECORD** naming the **word-sized key the
  attacher supplied** (not just a bit — a word can directly encode the
  waiting Saw task's identity, so kernel wake sources dispatch to
  userspace tasks with no side-table lookup — the async-executor
  integration point) plus a per-kind payload.
- **THE RESULT SHAPE, AMENDED** (Jul 29's `wait() -> (key, readiness)`
  register pair → this record; ratified Aug 16, user, as design 178 M2
  unit 3 rider 3 — the one place a ratified section has changed).
  The record is `key` word, `tag` word, payload words, with the sizes and
  word offsets published as constants and the tag a raw-backed enum (the
  §2 design-145 wire idiom) whose value space is extensible. Why: the KEY
  is universal and the PAYLOAD is not — a Channel's readiness is not a
  Timer's is not an Event's — so a fixed register pair would have had to
  be the union of every waitable's answer forever, and the register file
  is the one thing that cannot grow. The Event's payload is its
  accumulated word, which makes the common wait one syscall instead of
  two; it is a SNAPSHOT taken when the wait was answered, so a producer
  signalling again before the woken thread runs makes a following
  `receive` larger, and `receive` stays the authoritative drain.
- **Copy-out is checked, and it is one door.** This is the first place the
  kernel writes a process's memory. The destination must be word-aligned
  and inside the process's writable grant; one that is not TERMINATES the
  process (§5.7's faults ruling — a process linked its own image and knows
  where its data is). A single kernel funnel performs that check for
  everything that will ever copy out, §2.1's message body included; it is
  also the only place the kernel dereferences a user address, which is
  what makes the per-address-space mapping switch a one-place change when
  there is more than one address space.
- The buffer is the CALLER's, because the kernel has no allocator and
  nowhere to put one that would outlive the call. The typed Saw wrapper
  supplies it out of its own frame, so a raw address never appears in
  that surface.
- **`remove` TAKES THE KEY, and keys are UNIQUE per Waiter** (ratified Aug 16,
  user, as design 178 M2 unit 3 rider 4). Detaching edits the WAITER's own
  attachment table and never touches the waitable, so the authority it spends is
  the Waiter handle plus the key that names the attachment — a waitable's handle
  would be authority the operation does not use. It is also the only form that
  survives handle CLOSE: a closed waitable's stale attachment has no handle left
  to name it with. The invariant that forces: `add` with a key the Waiter
  already uses is a FAULT, because a duplicate makes two questions ambiguous at
  once — which attachment a `remove` names, and which one an answer came from.
- Waitables: Channel (readable / reply-ready), Event, Timer,
  Interrupt, ReplyHandle. **Event and Interrupt are BUILT** (M2 units 3
  and 4); the rest are M3. The second kind is what moved an attachment
  out of the waitable and into a table of its own: a Waiter's set has to
  be ONE list, since a wait scans it once and a `remove` walks it once,
  so per-kind lists would make both a matrix over kinds. What stays
  per-kind is four questions — who is watching me, am I ready, what does
  a record say — each an exhaustive match, so a fifth waitable cannot be
  added silently. **Attach semantics (ratified Jul 29):**
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
  - **ITS FIRST MIGRATION CASE ALREADY EXISTS** (design 178 unit 4): M2's
    boot-time device grant is a `sosimg` record a driver package declares
    in its manifest and the kernel installs before entering user mode,
    authorized against one window the board publishes. Everything a
    Mapping adds is what that placeholder lacks — a handle, a derivation,
    an op that installs it, and a close that revokes it — so the migration
    is "the same window, obtained rather than declared", and the M2 code
    says so where the check is.
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
- **Rights are a bitmask, SCOPED PER OBJECT KIND (ratified Aug 7,
  user).** Each kind defines its own backed rights enum —
  `SystemRight: UInt32 { case Transfer = 1, case Manage = 2, case
  Debug = 256, case Shutdown = 512 }`, `ChannelRight { … Send,
  Receive }`
  — spelled `SystemRight.Debug`, scoped by the ENUM (no flat
  name-mangling; exhaustive match + the design-145 wire discipline
  come free). Kind-specific bits OVERLAP freely across kinds: a
  rights word is only ever interpreted against its handle's kind,
  which dispatch establishes before the check (§3 order), so each
  kind owns 24 bits instead of sharing 32. With §3's typed handles,
  the check helper demands the MATCHING right type
  (`check(h: SystemHandle, r: SystemRight)`) — testing a channel
  right against a System handle is a COMPILE error. The exception:
  **UNIVERSAL rights own the pinned LOW BYTE — bits 0-7, identical in
  every kind's enum (ratified Aug 7, user: reserve room, more shared
  operations are coming).** Assigned so far: bit 0 = `Transfer` (may
  be sent over a channel — the movability capability), bit 1 =
  `Manage` (derive children); bits 2-7 RESERVED (candidates as they
  prove universal: wait/signal, introspect/info, revoke). Generic
  kernel paths (channel handle-transfer) check universal bits without
  knowing the kind; `sosabi` `static_assert`s each kind's enum
  against the pinned table so no kind can drift, and a kind-specific
  right below bit 8 is a spec violation the assert catches. **No DUPLICATE right — no handle
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
- **Typed handles in the Saw API (ratified Aug 7, user).** Each object
  kind gets a DISTINCT handle type in the `sos` module —
  `type SystemHandle = UInt`, `type ChannelHandle = UInt`, … — and the
  Saw-facing wrapper holds the typed handle (`System` stores a
  `handle: SystemHandle`; its `shutdown` method passes it to the raw
  exported `sos_system_shutdown`). Saw's distinct-alias rule gives exactly the
  wanted asymmetry for free: the typed handle FLOWS TO `UInt`
  implicitly (one zero-cost lowering at the `sos_syscallN` stub), but
  a raw word or a different kind's handle cannot flow IN — crossing
  into a handle type is an explicit construction, done at creation
  and, kernel-side, in dispatch AFTER table/generation/rights
  validation (the type then means "validated as this kind", not just
  "a number"). The typing stops at the ABI boundary: `@export`ed vDSO
  symbols and the syscall stubs keep raw `UInt` words (C callers see
  words; the export whitelist is primitives), and the kernel handle
  TABLE indexes by word. This is TIER ONE (kind safety) of two: when
  M2 brings closeable/transferable handles, the OWNING tier is a
  NoCopy struct wrapping the alias (deinit closes, `move` transfers —
  the TcpStream pattern, and §3's no-DUPLICATE rule is its exact
  NoCopy correspondence), with the alias as its payload — additive,
  not a migration. Adoption: the M1 candidate branch, as a
  review-round change.

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

1. ~~Channel semantics~~ RESOLVED §2.1 + **base-send DECIDED (user,
   Jul 31): STRICT RENDEZVOUS.** Every send blocks until a receiver
   takes the message — the kernel holds NO message buffers (bounded
   only by waiting senders; zero-copy handoff at the rendezvous).
   Async notification is the Event object's job (§2.4). Request/reply:
   `call` = rendezvous handoff + block on the ReplyHandle. Message
   limits (max body + max handles) still apply per message; concrete
   numbers are a kernel-brief constant (orchestrator pin, veto-able:
   64-byte body, 4 handles).
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
6. ~~Boot protocol~~ **DECIDED (user, Jul 31): KERNEL LOADS the root
   server** from a separate flash partition — real separation from
   day one; independent updates. To keep the loader microkernel-sized
   the image format is NOT ELF but a minimal flat **sosimg** header
   (orchestrator pin, veto-able: magic, version, entry offset,
   segment table of {flash_off, load_addr, len, flags}) that Blade
   emits as a build target. Kernel: parse header → map/copy segments
   (PMP/APM per §5.5) → mint root handles (boot channel, root
   MemoryObjects for RAM + device ranges, root IRQ table) → enter
   U-mode at the entry. Everything else derives from those handles.
7. ~~Syscall ABI~~ **DECIDED (user, Jul 31; object-uniform Aug 5): (status,
   value) PAIR, every call an OBJECT OP.** Conceptually every syscall is
   `object.method(args)` — there are NO bare numbered syscalls `[user,
   Aug 5]`. **The register convention, per profile (arm64 column ratified
   Aug 7, design 162 decision 1):**

   | Role | Profile A (riscv32) | Profile B (arm64) |
   |---|---|---|
   | handle in / status out | `a0` | `x0` |
   | op (a method id on that object's table) | `a7` | `x8` |
   | arguments | `a1`-`a5` | `x1`-`x5` |
   | value/handle out | `a1` | `x1` |
   | trap | `ecall` | `svc #0` |

   The two are the same shape one-to-one, deliberately: the op sits in a
   register OUTSIDE the argument run on both, so arguments are a clean
   sequence and neither profile has to shuffle. `x8` is where AArch64 Linux
   puts a syscall number, so every disassembler and every reader who has seen
   one arm64 syscall reads this one correctly. ONE difference is not
   cosmetic and belongs to the HAL rather than the kernel: `ecall` leaves the
   saved PC pointing AT the instruction and `svc` leaves it pointing PAST,
   so advancing on return is a per-profile decision (a kernel that advanced
   on Profile B would skip the instruction after every syscall). Returns:
   status word
   (0 = ok, else a small **`SosStatus`** enum tag — RENAMED from
   `SysError`, ratified Aug 7 [user]: an enum with an `Ok` case is a
   STATUS, not an error (`SosStatus.Ok`, `SosStatus.BadOp`, …), and
   the `Sos` prefix names whose status it is. The hosted runtime's
   `SysError` in rt/ABI.md is a SEPARATE frozen contract (errno tags,
   machine-parsed since design 149) and keeps its name; the spec notes
   the correspondence, nothing more. An `Err(SosStatus.Ok)` never
   arises by construction: the wrapper boundary splits on the status —
   `Ok` → `Result.Ok(value)`, anything else → `Err(status)`).
   Kernel
   dispatch is §3's shape verbatim: handle-table lookup → object type →
   op table → rights check → op. Even the M1 primitives conform: a
   **System object** (kernel singleton; see §2 table) is minted to root
   at boot, and `debug_print` / `shutdown(status)` are its first ops,
   rights-gated (`SystemRight.Debug`/`.Shutdown`, §3) — a process without the System
   handle cannot even print. `exit` is deliberately absent: process exit
   is a Process-object op when Process objects land (ratified Aug 5); in
   M1's one-process world, root's `system.shutdown(status)` ends the run
   (QEMU: the sifive_test write). Maps 1:1 onto the
   `sos` module's typed wrappers (`system.debug_print(...)`,
   `-> Result<T, SosStatus>` — auto-wrap does the rest).
   **API ownership (ratified Aug 5, revising the Jul-31 line):** the `sos`
   module is U-MODE LIBRARY CODE (never kernel-mode — that half of the
   Jul-31 decision stands) but it is OWNED AND EXPORTED BY THE KERNEL
   PACKAGE — the Zircon-vDSO discipline: syscall/method NUMBERS ARE NOT
   ABI. They live in one kernel-internal constants file shared by the
   dispatch tables and the wrappers, so the two can never skew and the
   kernel may renumber freely; userspace links the exported module and
   never sees a raw number. Delivery is static linking in v1 (the
   manifest/module-path mechanism); a true mapped-vDSO is an
   object-model-era upgrade that changes nothing about the shape. The
   kernel package ALSO exports a C-ABI surface for non-Saw languages
   (`@export`, whitelist-clean — handles/statuses/ops are integers):
   per-op functions (`sos_system_shutdown(handle, status)`) as the
   supported C interface, over fixed-arity raw forms
   (`sos_syscall1(h, op, a)` — no varargs across the trap boundary),
   over the per-arch HAL `ecall`/`svc` stub. One implementation chain,
   three entry altitudes: typed Saw, typed C, raw.

## 5b. Two machine profiles, two architectures (DECIDED, user, Jul 31)

SOS targets TWO first-class profiles from the beginning, both
QEMU-runnable for a fast dev loop:
- **Profile A — MPU**: riscv32, QEMU `virt` first, ESP32-P4 hardware
  later (the P4 IS this profile: M+U modes, PMP/APM, no per-process
  paging). Single multitask image, physical addresses, coarse
  protection, small-N domains. 32-bit.
- **Profile B — MMU**: arm64 (aarch64), QEMU `virt`. Kernel at EL1,
  processes at EL0, real per-process page-table AddressSpaces. 64-bit.
  (arm64 moots the RISC-V S-mode/SBI question: QEMU enters EL1
  directly; no firmware protocol needed for M1-class work.)

Rationale: multi-arch + 32/64-bit awareness from day one; the
object/handle/channel/syscall model is IDENTICAL across profiles —
divergence is confined to a small per-arch HAL (boot, trap entry,
context switch, Mapping/AddressSpace implementation), selected at
build time via the module-path mechanism (`--module-path
hal=sos/hal/<target>/kernel` — Blade/B0 machinery). All wire/boot formats
(sosimg, message headers) use FIXED-WIDTH fields (the design-47
discipline) so 32/64-bit profiles interoperate. AddressSpace on A =
PMP region set (+APM on P4); on B = page-table root. Roadmap: M1 =
riscv32 boot-to-root-server (design 78); M1b = arm64 EL1 boot parity
+ HAL extraction (design 162) BEFORE object-model work; then the
object model lands once, two-profile-tested.

**BOTH PROFILES ARE LIVE (design 162, Aug 7).** The claim above is no
longer a plan: `make sos-test` boots the same kernel on
`qemu-system-riscv32 -M virt` and `qemu-system-aarch64 -M virt -cpu
cortex-a53`, twelve cases each, and either failing is red. What the port
cost, and what it proved:

- **The kernel has no architecture in it.** `sos/kernel/core/` names no
  register, no trap cause, no protection hardware and no board; it reaches
  all of it through one module it imports as `hal`, mapped per build to
  `sos/hal/<arch>/kernel/`. The harness SCANS that directory for
  architecture names and fails the run on a hit, comments included —
  because a leaked constant still compiles and is only wrong on the profile
  nobody happened to be building.
- **The HAL is the whole of the difference**, and it is small: boot +
  vectors, the privilege transition, the protection primitive, a console
  byte sink, a way to stop the machine, the trap-frame accessors, and the
  board's memory map. Everything else — dispatch, rights, the loader's
  order of checks, the console's FORMATTING — is shared.
- **Profile B's isolation is a static identity map** (design 162 decision
  2): one map built at boot, EL0 default-deny, and the only mutable part is
  the EL0 permission bits of the pages a root image was granted. That is
  PMP parity, not paging — Mapping/AddressSpace objects stay M2.
- **The same root server sources build for both.** `sos/root/src/` is
  unchanged between profiles; only its manifest's `[sos.<triple>]` section
  differs, and Blade grew per-target sections plus an ELF64 reader to make
  that true.
- **Images are arch-tagged** (sosimg v2): a wrong-profile image is a clean
  load error, tested on both. Before the tag, the two profiles' headers
  were byte-compatible wrappers around incompatible instructions and the
  only thing stopping one booting on the other was that nobody had tried.

## 5c. The native floor (design 172, both parts, Aug 7)

SOS is written in Saw, and the exceptions are ENUMERATED rather than tolerated.
Every surviving line of C states, in the file that holds it, why it is not Saw —
and there are only three reasons in the whole system:

1. **An INSTRUCTION with no Saw spelling.** `ecall` / `svc`, `mret` / `eret`,
   the vector tables, `csrw` / `msr` / `mrs`, `dsb` / `isb` / `tlbi`, the
   semihosting `hlt`, and the register pinning each of them needs. Inline asm in
   Saw is a separate design conversation and deliberately not one this milestone
   opened.
2. **`memcpy` / `memset` / `memmove`.** A byte-copy loop written in Saw is
   exactly the pattern LLVM's loop-idiom recognizer rewrites INTO a call to
   `memcpy` — which, in a freestanding build where this IS `memcpy`, is a call
   to itself. C compiled with `-fno-builtin` is the supported way to say "do not
   do that", and it is why every libc writes these in assembly or with the same
   flag. PERMANENT. (The `__atomic_*` libcalls beside them are the same shape:
   the caller is codegen, not source.)
3. **A LINKER SYMBOL's ADDRESS.** Saw cannot name one — `extern` declares only
   functions, an extern function is not usable as a value, and `@export` on a
   static emits a definition rather than a reference. Four accessor bodies, two
   per profile. Filed as DF-172a.

What that cost, measured: the C went from 383 code lines to 135, a 65%
reduction, over two passes.

Part 1 took it to 207, nearly all of it out of the two kernel HALs — `sink.c` is
170 code lines to 47 on arm64 and 75 to 22 on riscv32. What moved: both board
consoles and both machine-stops, the arm64 static identity map and its grant
editing, the riscv32 PMP region staging, and the kernel-fault report with its
hex formatting.

Part 2 took it to 135, out of the two places part 1 could not reach. The bump
arena and the four `__saw_rt_*` seams are Saw in `sos/rt/common/src/lib.saw`,
one copy serving the kernel and every process; the process side's two hooks and
its parked boot handle are Saw in `sos/kernel/sysapi/`, beside the System object
whose authority they spend. Both user HALs are now their syscall instruction and
nothing else, and `sos/rt/common_c/support.c` is `mem*` and the atomic libcalls
— reason 2, and reason 2 only.

So the floor is one shared C file plus four inline-asm leaves, and every one of
them is reason 1 or reason 2. Reason 3 is the only open language gap.

**The panic path is the interesting one.** The console writer the runtime seams
call is now Saw, and it is CHECK-FREE BY CONSTRUCTION rather than by
inspection: raw pointer reads, wrapping arithmetic, no indexing, no allocation.
That is what makes a panic raised inside the panic reporter unreachable instead
of merely unlikely, and it was verified from emitted IR before the code shipped
— the whole call cone contains no bounds check, no overflow trap and no call
back into `__saw_rt_panic`. A harness case pins it on both machines by taking a
compiler-raised bounds check and asserting the message arrives in three
independent pieces.

**What blocked part 2, and what unblocked it** (DF-172e, now CLOSED). The seams
were the one place the diet stalled on the LANGUAGE rather than on effort. Every
part of the move had been probed and worked — the arena is expressible,
`--runtime-provider` permits and checks the exports, and `sosrt` is already a
dependency of both the kernel and every process — except one signature.
`rt/ABI.md` freezes `__saw_rt_panic` as `noreturn`, and the only things that
produced `Never` were `panic()`, which is what the seam IS, and an `extern`
already declared noreturn, which Profile A lost when its finisher write became
Saw. Design 177 supplied the missing producer: a conditionless `while { }` with
no `break` types `Never`. The seams landed unchanged in every other respect,
which is what the probing bought.

**Who declares the runtime.** `@export`ing a frozen `__saw_rt_*` name needs the
COMPILE to say it implements the ABI (design 149), so `tools/sos_runner.py`
passes `--runtime-provider` for kernel images (a kernel is not a Blade package)
and a process image carries `[package] runtime = true`. The seam bodies arrive
from a dependency in both cases, which is sound because the flag describes the
compile and a package build compiles its whole module graph into one unit.

## 6. Explicitly NOT in the kernel

Drivers (userspace via Interrupt + MMIO MemoryObjects), filesystems,
network stacks, POSIX personality, dynamic linking/loading, package/
process management policy, time-of-day (a userspace service over the
Timer primitive). The kernel knows objects, handles, rights, threads,
memory words, and nothing else.

## 7. Scheduling (ratified Aug 3)

- **Fixed-priority preemptive, 8 system levels (0–7).** Ready queue =
  per-level FIFO + a one-BYTE ready bitmap; pick-next = find-first-set
  + pop, O(1), a handful of instructions on both profiles. Idle is NOT
  a level — a per-profile WFI loop runs when the bitmap is empty.
- **Round-robin within a level**, fixed timeslice (pin, veto-able:
  10 ms), tick from the per-profile timer (CLINT mtimer / ARM generic
  timer — the same source the Timer object needs). Preemption is
  immediate: readying a higher-priority thread switches on the way out
  of the kernel; same-priority never preempts mid-slice.
- **Priority bands, not raw levels.** Processes NEVER see or name
  system levels. The only priority surface is
  `enum Priority { Background, Low, Normal, High }` in the userspace
  `sos` module, with FIXED ABI tags 0–3 (the wire representation never
  changes). The thread-spawn syscall carries the band TAG; no syscall
  accepts a raw system level.
- **Per-process band→level map, kernel-side.** Each Process object
  holds a 4-slot map resolving band → system level; the kernel
  resolves at thread-spawn — the map is the ENFORCEMENT point (a
  process cannot escape its band with raw syscalls). A thread's
  system priority is resolved ONCE at spawn and stored plain — the
  scheduler hot path is numeric-only. Initial default map:
  `Background→0, Low/Normal/High→1` (apps cluster at 0–1; levels 2–7
  are headroom for drivers/services). Low/Normal/High are declarative
  until a deployment differentiates them — intended.
- **The map comes from build-time metadata + launcher policy.** The
  process image declares its requested map in `sosimg` metadata (a
  4-byte header field; Blade emits it from the package manifest, e.g.
  `[sos] priorities = { background = 0, low = 1, normal = 1,
  high = 1 }` in Saw.toml). Metadata is a REQUEST, not authority: the
  launcher reads it and may honor, clamp, or override; the kernel
  stores whatever map the launcher passes to `create_process` and
  never parses metadata for policy. The root server's own map is
  applied verbatim from its image at boot (the kernel parses that
  image anyway; root is trusted by construction).
- **Launching is a capability.** `create_process` requires a
  specialized LAUNCH capability, minted at boot to the root server.
  Ordinary processes cannot create processes in v1 — they ask the
  launcher service over a channel.
- **The map is immutable after creation.** No remap syscall, no
  self-modification, no visibility into the map from inside the
  process (each process sees only its 4 named bands). Changing a
  running process's priority = restart it. (A future dynamic
  re-prioritization design could add a LAUNCH-gated syscall without
  disturbing anything here.)
- **No priority inheritance.** Inheritance chains through transferable
  ReplyHandles are ill-defined (the reply obligation migrates). The
  mitigations: (a) the CONVENTION that servers run at ≥ the max
  priority of their clients (the launcher assigns both, so this is
  enforceable policy); (b) the **direct-switch fastpath** — on a
  rendezvous handoff, switch straight to the receiver when runnable,
  which removes most incidental inversion with no donation semantics.
  MCS-style budgets/inheritance remain possible later designs.
- **Uniprocessor kernel in v1**, both profiles (P4 is dual-core and
  QEMU can do SMP; SMP is its own future design — locking model,
  per-core queues — and nothing above precludes it).

## 8. Thread & process lifecycle (ratified Aug 3)

- **A thread fault kills its process.** The process exits with a
  fault status; there is no per-thread fault recovery (a faulted
  thread shares mutable state with its process — "keep running minus
  one thread" is silent corruption). Kernel teardown-on-exit/fault
  (§2 table) reclaims everything unconditionally.
- **No join syscall; no thread kill.** Threads are expected to be
  POOL WORKERS: created at startup, draining task queues, dying at
  process exit. Work completion is a task-level concept (channels/
  Events), not a thread concept. A wedged/hostile thread is a
  process-level problem by the fault rule.
- **Thread handles are waitables** (level-triggered ready when the
  thread has FULLY exited) — the observability that join traditionally
  provides, needed only for safe stack reclamation when a dynamic pool
  scales down (thread stacks are userspace memory passed to
  `thread_create`; reuse before real exit is a use-after-free). v1
  fixed pools never use it.
- **Process handles are waitables** (ready on exit, any cause) — the
  primitive userspace supervision parks a Waiter on.
- **`process.get_status()`**, gated on the WAIT right: ONE fixed-width
  status word — kind in the high bits (`Exited | Faulted | Killed`),
  code in the low bits (exit code, or a fault-cause tag). Detailed
  fault forensics (faulting PC/address) is a later design.
- **`process.kill()`**, gated on a KILL right. Cooperative-first
  teardown stays the norm; kill is the capability-gated escape hatch
  that makes userspace supervision real against wedged/hostile
  processes. (This resolves §4's kill-tree question: kill exists,
  process-granular only, no kernel trees — supervision topology is
  userspace's.)

## 9. Interrupt delivery (ratified Aug 3)

- **Mask-on-fire, ack-to-rearm.** IRQ fires → kernel masks the line +
  marks the Interrupt object ready (wakes per Waiter rules). At most
  one UNACKED fire exists per Interrupt, ever. `irq.ack()` unmasks;
  a still/again-asserting device re-fires — correct level semantics.
  Level-triggered Waiter readiness means NO interleaving loses a
  fire (readiness persists until consumed).
- **Ack is a RELEASE.** The discipline: everything that touches device
  registers happens BEFORE ack; post-ack code runs only on data
  already extracted. Under it, a second worker entering the pre-ack
  section while the first runs its post-ack tail is PIPELINING (a
  throughput feature), not a race. Drivers that need post-ack register
  work serialize with a Mutex — their choice; the kernel does not
  enforce single-servicing.
- **v1 canonical driver shape: ONE task owns one Interrupt.** A single
  cooperative servicer can never race itself — the ack-position
  question evaporates. Multi-threads-on-one-Waiter is for servers
  multiplexing independent streams, not for a device IRQ.
- **The serve idiom needs no kernel support**: a `sos`-module closure
  wrapper that acks ON HANDLER EXIT gets non-reentrancy directly from
  mask-until-ack (the line is masked for the whole handler body — the
  "mask while handler runs" mechanism IS the interrupt mask). An
  explicit mid-handler ack is the deliberate opt-in to pipelining.
- **Combined form (pin): `waiter.wait(ack: irq_handle)`** — atomically
  ack, then block. Pure syscall-halving for the hot loop (one syscall
  per interrupt); not needed for correctness (level-triggering already
  covers the gap). One optional arg on wait. STILL A PIN after M2 built
  the object: the echo driver runs the two calls separately and the
  transcript does not notice.
- **BUILT M2, and what the implementation added to this section**
  (design 178 unit 4):
  - **The wait ANSWER carries the LINE** — `WaitPayload.Interrupt(line:)`
    through §2.2's copy-out record. There is no fire COUNT, because the
    mask-on-fire rule makes it always one; a dispatcher parked on several
    lines gets which one without a side table, exactly as it gets the key.
  - **An ACK WITH NO FIRE OUTSTANDING IS A FAULT**, not a tolerated no-op.
    The only way to learn about a fire is to be told about one, so an
    extra ack is a servicer that will ack the NEXT fire without servicing
    it — a dropped byte or a wedged device with no first symptom.
  - **THERE IS NO MASK OP AND NO MASK RIGHT.** Masking is the kernel's
    half of the cycle; a driver that could mask at will could leave a line
    masked, which is a denial of the device with no diagnostic. What a
    driver controls is its own device's interrupt-enable register, through
    the window it was granted.
  - **"NOTHING RUNNABLE" STOPPED MEANING DEADLOCK.** An Interrupt is the
    first readiness that arrives from outside the set of runnable threads,
    so the kernel idles when any line is bound and reports the ratified
    deadlock otherwise. It idles by PARKING THE CORE AND POLLING rather
    than by taking the trap, which leaves design 178's D2 exactly as it
    was: the kernel still never takes an interrupt in kernel mode.
  - **THE MMIO GRANT IS DECLARED BY THE IMAGE** (finale constraint 1,
    ruled Aug 16, user): a `sosimg` device record, emitted from the
    driver package's own manifest, authorized against a window the board
    publishes. It is the M2 PLACEHOLDER for §2.5's Mapping and is that
    section's first migration case.

### 9a. The console handover protocol (ratified Aug 16, user)

Both v1 machines have ONE console UART, and both the kernel and a driver
process can drive it. Unstated, that is a flaky-test mystery — two
writers on one device interleave mid-word and neither is wrong.

- **Boot**: the kernel owns the device outright and narrates.
- **Handover**: entering user mode writes ONE marker line, and it is the
  last thing the kernel says on its own initiative. After it, THE PROCESS
  OWNS THE DEVICE.
- **Reclaim, on diagnostics only**: a fault report, a process teardown, a
  panic, or an interrupt on a line nothing is bound to. Interleaving
  there is acceptable because there is no longer a transcript to protect.

A `SystemOp.DebugPrint` is NOT a violation: that is the process asking
the kernel to place a byte, and the process is the one sequencing it.
What the protocol forbids is the kernel narrating over a process that is
using the device. The one bounded exception is a kernel that arms a
timer, whose first few ticks are narrated after handover — which is why a
case whose transcript matters after handover arms none.

## 9b. Kernel sync primitives: IntrSpinLock (ratified Aug 6)

- **std owns `SpinLock<T>`** (design 149): safe cross-context lock, any
  environment with real atomics, userspace included. **The KERNEL owns
  `IntrSpinLock<T>`** — SpinLock composed with interrupt masking, which
  is privileged (mstatus.MIE / DAIF) and therefore cannot exist in
  userspace even in principle.
- Semantics (the spin_lock_irqsave discipline): `lock` SAVES current
  interrupt state and disables BEFORE spinning (an ISR must not arrive
  mid-spin and deadlock on the same core); the epilogue RESTORES, never
  blindly re-enables — nesting works, irqs-off callers stay irqs-off.
  Saved flags are per-acquisition, carried by the closure scope. The
  body inherits `sync` and carries the latency contract: irqs-off time
  IS interrupt latency — short sections only.
- Mechanism: two per-arch HAL one-liners, `hal_irq_save() -> Flags` /
  `hal_irq_restore(Flags)`, under `sos/hal/<arch>/kernel/`. The CAS
  stays even on uniprocessor (SMP-future correctness; under `+a` it is
  cheap; a FAILED acquire on uniprocessor signals reentrancy — panic,
  never spin forever).
- Timing: DESIGN ratified now; IMPLEMENTATION lands with the M2-era
  interrupt work — M1 never sets MIE, so the type would be untestable
  dead code before then.

## 10. Userspace runtime: HandlerGroup + the wake bridge (ratified Aug 3)

Saw's sequential surface is UNCHANGED: `channel.call()`, `receive()`,
`read()` suspend in place on plain tasks — colorless straight-line
code stays the substrate, and TaskGroup remains exactly what it is
today (a lifetime/join scope for tasks on a thread pool). The
event-driven EDGE of a process gets a second, distinct construct:

- **`HandlerGroup`** — a group of waiting HANDLES running on a task
  pool; the userspace face of the kernel Waiter, one-to-one (TaskGroup
  ↔ threads/tasks; HandlerGroup ↔ Waiter/handles). Deliberately NOT
  bolted onto TaskGroup: attachments are persistent subscriptions
  (removed, never "joined"), and K workers service M handles (no
  frame-per-handle).
  ```saw
  var dispatch = HandlerGroup(workers: 2)
  let id  = dispatch.add(move timer)  { t, fired -> ... }
  let cid = dispatch.add(move server) { ch, msg, req -> ... }
  let timer = dispatch.remove(id)     // coat check: ownership back
  // Deinit: detach all, close unclaimed handles, cancel workers
  ```
- **Ownership: move-in, forced by the language.** Handles are NoCopy
  and references are non-escaping — a HandlerGroup CANNOT store
  `&Timer`, so group-owns-while-attached is the only representable
  design, exactly mirroring unique kernel handles. `add` returns a
  distinct-typed `AttachmentId` (NOT authority — meaningless without
  the group). `remove(id) -> Box<any Waitable>?` returns ownership
  (recover the concrete type with `take<T>()`; typed sugar can come
  later); None = stale id.
- **Per-attachment NON-REENTRANCY, guaranteed**: one handle's handler
  never runs concurrently with itself (in-service flag; readiness
  arriving mid-handler is deferred and re-dispatched on completion).
  Generalizes the one-task-per-Interrupt decision to every handle
  kind as a stated guarantee.
- **The handler BORROWS the source per invocation** (`&Source`, a
  scoped non-escaping lend — the `with_ref` shape), which is sound
  precisely BECAUSE of non-reentrancy: at most one borrow live per
  attachment. Per-kind signatures: Timer → `(&Timer, fired)`;
  Channel → `(&Channel, msg, RequestHandle)` — the RequestHandle is
  fresh PER MESSAGE and moves in (forwardable, per §2.1 delegation);
  Interrupt → `(&Interrupt)` with ack-on-exit default (§9).
- **Cross-handle parallelism up to `workers`.** Parallelism WITHIN one
  source stays explicit user code (spawn from the handler body into a
  TaskGroup) — the concurrency decision is visible, never implicit in
  a dispatch engine. Handler bodies are ordinary task code and may
  suspend; a suspended handler parks its worker, others keep
  dispatching. Backpressure is free: all workers busy → readiness sits
  (level-triggered) and rendezvous senders block; nothing buffers,
  nothing drops.
- **The wake bridge is ONE mechanism**: a parked task's wake-word is
  the Waiter KEY supplied at attach; kernel readiness returns the key;
  the executor marks that task runnable. Handlers, blocking-shaped
  calls, and raw waits all ride the same path (this is what §2.2's
  word-sized key was designed for). Implementation freedom (runtime
  brief, not spec): whether a HandlerGroup owns its own kernel Waiter
  or registers through the process runtime's reactor Waiter — the
  kernel permits both (one Waiter per handle; many Waiters per
  process).

## 11. What is built, and what remains

- ~~Design (with user): root server responsibilities~~ RESOLVED §12
  (ratified Aug 5).
- **Orchestrator pins SETTLED by M1/M1b/M2:** the rights-word bit
  assignments and the per-object op tables are concrete and
  kernel-internal (`sos/kernel/abi/`, one file shared by the dispatch and
  the wrappers — §5.7's vDSO discipline is what keeps them renumberable);
  the per-profile kernel memory layout is two linker scripts under
  `sos/hal/<arch>/kernel/` over per-type static slabs; the `sosimg`
  header is concrete at v3, the §7 priority-map field included.
- **Orchestrator pins STILL OPEN:** where the physical-region refcount
  lives (§5.9), which nothing can answer until something allocates
  physical regions; §2.1's message limits (pinned 64-byte body / 4
  handles, unbuilt with the Channel); and the §7 priority map plus root's
  bootstrap band map, which the loader parses and REPORTS while no
  Process slot stores either.
- **What the kernel does NOT have after M2.** One area per line, with the
  section that specifies it; design 232 is the M3 plan of record and is
  pointed at rather than duplicated.
  - **Clock and Timer** (§2 rows) — neither object exists; the hardware
    timer is the scheduler tick and nothing else, so a process cannot
    sleep at all. Design 232 unit 1.
  - **A second process** (§2 Process row, §12) — `create_process` is
    absent and exactly one Process is ever built, so §12's
    loader-above-boot rule has never been exercised. Design 232 unit 2,
    with the launch flow (`give`, and the boot-handle iterator the child
    drains) in unit 3.
  - **Memory / IoMemory / MemoryMapping and `map()`** (§2.5) — no memory
    object of any kind; a process gets one granted range plus a writable
    window from the loader, and the M2 device grant is the declared
    placeholder that surface retires. Design 232 unit 4.
  - **Quotas** (§12's creation-authority pin, which M2 ANSWERED with a
    factory-capability rights bit rather than a quota) — the per-process
    table, `QuotaExceeded`, and creator-pays accounting are design 232
    unit 5. A full slab answers `SosStatus.NoResource` today.
  - **Channels and ReplyHandle** (§2.1) — M4; the one fully ratified
    object surface with no implementation at all.
  - **Handle close and generations** (§3) — no op table has a close op
    and a handle entry carries no generation, so §3's stale-handle
    detection and §4's owning NoCopy tier both wait on it.
  - **Priorities** (§7) — nothing of §7 is built; round-robin is ruled to
    stay through M3 (design 232 agenda item 10).
  - **Thread and process waitability, and `kill`** (§8) — attaching
    either kind is a fault today; `kill` has no op and no right.
  - **Kernel interruptibility, `IntrSpinLock` (§9b), SMP** — design 178's
    D2 holds (interrupts are taken from user mode only); design 232 pin 1
    rules preemption points into M3 as unit 1.5, and SMP waits for
    channels.
  - **`HandlerGroup`** (§10) — userspace runtime work rather than kernel
    surface, and unbuilt: a process wires a Waiter by hand today.
  - **The mapped vDSO** (§5.7) — delivery is still static linking, which
    changes nothing about the shape.
- **Roadmap (brief numbers assigned at dispatch — the design-78/79
  references in §5b are stale):** M0 riscv32 QEMU target (design 112) →
  M1 riscv32 QEMU boot-to-root-server → M1b arm64 EL1 parity + HAL
  extraction → M2 the concurrent kernel, landed once and two-profile
  tested → M3 the multiprocess kernel (design 232).
  - **M0 DONE (design 112):** Profile A substrate is live —
    `sos/kernel/` (boot.S + virt.ld + rt.c runtime seams + a Saw `main.saw`
    whose NS16550A UART driver is built on `UnsafeMemory<_, Device>`),
    booting under `qemu-system-riscv32 -M virt -bios none` at RAM base
    0x8000_0000, printing a banner and exiting cleanly via `sifive_test`
    (0x5555 = exit 0). A boot trap stub FAILs the run (never hangs) on a
    fault, and the freestanding panic seam writes to the UART then FAILs.
    `make sos-test` (tools/sos_runner.py) is the mechanical loop the kernel
    briefs build on. sawc's freestanding profile gained the enabling
    dead-code-strip (internalize non-exports + per-symbol sections) so a
    kernel links only what it reaches; the Saw object is `rv32i`/ilp32
    soft-float (llvmlite's default for the triple), boot.S/rt.c assembled
    `rv32imac_zicsr`.
    M1 changed two things in this description: the M0 trap stub is now the
    KERNEL-mode half of a two-way trap entry (`kernel_fault`, byte-identical
    behaviour), and `sos/kernel/main.saw` no longer prints-and-exits — it loads
    root. The M0 banner/panic/trap cases are still green as
    `no_root_image` / `panic_seam` / `trap_fault`.
  - **M1 DONE (design 140), branch PARKED for user review:** riscv32
    boot-to-root-server. A real `mtvec` handler with `mscratch` as the mode
    witness (0 in the kernel, `&_trapframe` in U-mode) splits a syscall from a
    kernel bug in one branch; a U-mode trap saves 31 GPRs + `mepc` into a
    32-word frame, runs the Saw handler `ktrap`, and resumes. M-mode kernel /
    U-mode root, isolated by PMP TOR regions — U-mode default-deny does the
    work, so the kernel, the UART and the finisher are protected by never being
    granted. EVERY syscall is an object op per §5.7: a0 = handle, a7 = op,
    args a1-a5, and dispatch is §3's shape verbatim — handle-table lookup ->
    object type -> op table -> rights check -> op. The v1 object is the
    **System** singleton (§2) with `debug_print` and `shutdown(status)`, gated
    on `SystemRight.Debug` / `.Shutdown`; root receives its handle in the first argument
    register at entry (§12's boot handle set, one handle wide today). A bad
    handle, a bad op or a missing right returns a `SosStatus` and the
    process runs on; a FAULT is fatal and prints a cause tag (M0's never-hang
    discipline, kept). API ownership follows the vDSO discipline: the typed
    wrappers are a public `sos` module OWNED AND EXPORTED BY THE KERNEL PACKAGE
    (`sos/kernel/sysapi/`), every number lives in one kernel-internal package
    (`sos/kernel/abi/`) shared by the dispatch tables and those wrappers, and a
    process links `sos` and never writes a number. A per-op C-ABI surface
    (`sos_system_debug_print`, `sos_system_shutdown`) sits beside the Saw one
    over a fixed-arity raw `sos_syscall1` over the per-arch stub — one chain,
    three altitudes. The §6 boot protocol is
    concrete: a flat **sosimg** (16-byte header + 20-byte segment records, all
    fixed-width little-endian per design 47, carrying the §7 priority map),
    emitted by a Blade `emit = "sosimg"` build target reading the package's
    `[sos]` manifest section, appended to the kernel image as a `.payload` blob
    with linker-symbol bounds. The layout lives ONCE, in the shared
    `sos/imgformat/` package: Blade consumes it as a path dependency and emits
    bytes through it, the kernel consumes it through `--module-path` and reads
    images by overlaying the same structs as `UnsafeMemory` typed views, with
    `static_assert` pinning the sizes on both sides. `sos/root/` is a real
    separate Saw package built by Blade; it prints its banner THROUGH a System
    op — it holds no device grant and could not print any other way — and calls
    `shutdown(0)`. The kernel validates an image in full before placing a byte
    of it, so a segment aimed at the kernel is refused rather than obeyed.
    `make sos-test` is 11 cases including the two-image boot, a root that
    oversteps its grant, and one that makes bad calls and checks the statuses.
    Structure: `sos/kernel/core/lib.saw` is shared by every kernel image, which
    keeps them all on the same trap path; `sos/rt/common/` (Saw — since design
    172 part 2 the runtime seams and the arena too) and
    `sos/rt/common_c/support.c` (the C that must stay C: `mem*` and the atomic
    libcalls) are shared by the
    kernel and every process; and the architecture lives in
    `sos/hal/riscv32/{kernel,user}/`, each with an ABI.md, so M1b (design 162)
    ADDS `sos/hal/arm64/...` without moving any of it.
  - **M1b DONE (design 162), branch PARKED for user review:** arm64 EL1
    parity and the HAL extraction. The M1 feature set above runs identically
    on `qemu-system-aarch64 -M virt -cpu cortex-a53`: twelve cases per
    architecture, twenty-four total, either failing red. The M1 claim that
    "the architecture lives in `sos/hal/`" turned out to be half true — the
    kernel still held a UART register block, an `mcause` enum, PMP wrappers,
    `mepc + 4` and the board's memory map — so unit 1 moved all of it behind
    a module the kernel imports as `hal` (§5b has the summary). Profile B's
    HAL: `boot.S` (EL1 entry, sixteen exception vectors, `eret` to EL0),
    `sink.c` (PL011, semihosting `SYS_EXIT`, and the static identity map),
    `lib.saw` (the Saw surface: driver, trap frame, ESR decode, memory map),
    `virt.ld`, and a user-side `svc` stub — each with an ABI.md. Four things
    worth knowing beyond the brief:
    (a) **FP/SIMD must be enabled at EL1 before any compiled code runs.**
    `CPACR_EL1.FPEN` traps Advanced SIMD out of reset and LLVM vectorizes
    ordinary loops, so the first page-table loop faulted before the vectors
    could report it. FP state is NOT saved across a trap; with one thread and
    no preemption nothing observes that, and M2's context switch is where it
    stops being true.
    (b) **Semihosting, not PSCI, stops the machine.** PSCI `SYSTEM_OFF`
    always exits 0 and this harness asserts on exit STATUS — one case encodes
    its whole verdict in the number.
    (c) **Cortex-A53 is ARMv8.0** and has no LSE atomics, contrary to the
    brief's decision-3 note; `ldxr`/`stxr` exclusives cover everything the
    kernel and `SpinLock` need, so nothing was blocked.
    (d) **The two profiles report the same fault names.** ESR's exception
    class plus the data-abort direction bit decode to `store-access-fault`
    and friends, so the harness asserts one string against both machines.
    Also landed: sosimg **v2** with an `arch` tag (a wrong-profile image is a
    clean load error, tested both ways), a loader check that a segment is
    aligned to the target's grant granularity (a page here, four bytes
    there — without it root's code could become writable because its data
    started 200 bytes later), the kernel's hex output following the target's
    WORD width instead of a hardcoded eight digits, Blade reading ELF64 as
    well as ELF32 and refusing an address past the format's 32-bit field
    rather than truncating it, and `[sos.<triple>]` manifest sections so one
    root package builds for both profiles with `src/` unchanged.
  - **M2 DONE (design 178), integrated Aug 16:** the concurrent kernel —
    threads, a scheduler, Event, Waiter, the Interrupt object, and a UART
    echo driver running in USERSPACE on both machines. Six object kinds
    where M1b had one, and the dispatch shape of §3 did not move for any
    of them. Four units, each per-arch-gated; the harness ends at 32 cases
    per architecture, 64 runs, either machine failing red.
    (a) **The interrupt seam.** Each HAL gained twelve names and the kernel
    reaches interrupts through those and nothing else (`is_interrupt`,
    `irq_claim`/`irq_complete`, `irq_mask`/`irq_unmask`, `timer_start`/
    `timer_rearm`/`timer_pending`, and a selftest line). `ktrap` decides
    interrupt / fault / syscall IN THAT ORDER — an interrupt's instruction
    has not run, so it must never reach the syscall return path that steps
    the saved PC — and `service_irq` is the one arch-free entry: claim,
    rearm or mask (§9), run the hook, complete.
    (b) **Thread and Process (D4), the scheduler (D3).** The trap frame IS
    the thread context, so a context switch is `ktrap` RETURNING A
    DIFFERENT FRAME than it was called with: one switch point, at the
    user-return boundary by construction, which is D2 enforced by shape
    rather than promised, and no half-saved state to protect.
    `kcore.start_process` is the only way into user mode — a kernel that
    could enter it without a Process would have syscalls no handle table
    answers, which is what M1 was. Scheduling is ONE round-robin FIFO with
    no levels and a one-tick slice (§7 carries the status note).
    **The faults ruling landed whole** (178 pin 6, ratified Aug 8):
    `BadHandle`, `BadOp` and `AccessDenied` are no longer statuses but
    `FaultReason` tags that TERMINATE the offending process while the
    kernel stays up, reports, and runs the ratified teardown. It had to be
    uniform — an unbound handle names no KIND, so there is no op table to
    ask which rule applies — and it reverses the M1 line above. What
    survives as a status is `NoResource`, which a caller could not have
    known. Op tables and rights sets are named for the §2 object, spelled
    out (`SystemOp`/`ProcessOp`/`ThreadOp` beside `SystemRight`/… ; ruled
    at review, user, Aug 15).
    (c) **Event and Waiter (D5), and two funnels.** Level-triggered is an
    ABSENCE: nothing records that a waiter has been told, so `Wait` SCANS
    the attachment set — and three ratified sentences fall out of that one
    decision rather than being coded separately (a signal before anyone
    waits is not lost, an event still ready wakes the next waiter, and
    attaching an already-ready handle reports immediately). `write_result`
    became the only place an answer reaches a frame, its three entry points
    named in its docstring, which is what makes "exactly one write per
    syscall" checkable; `waitable_slot` is the only place waitability is
    decided, one exhaustive match, so a further waitable kind cannot be
    added silently. Three riders amended ratified text: an argument
    encoding is API (`create_event(mode:)`), the wait ANSWER became a
    validated copy-out record through the `copy_out` funnel (§2.2), and
    `remove` names the KEY with keys unique per Waiter (§2.2).
    (d) **The Interrupt object and the milestone proof.** The second
    waitable kind moved the attachment OUT of the waitable into a third
    object — (waiter, kind, target, key) — because a Waiter's set has to be
    ONE list; what varies per kind is four small functions in one place,
    each exhaustive. "Nothing runnable" split in two (§9): idle when any
    line is bound, the ratified deadlock report otherwise, with the idle
    path POLLING rather than taking the trap, which is what leaves D2
    untouched. The device window is a `SegFlag.Device` record in the
    driver's own `sosimg`, authorized against the one window each board
    publishes (§2.5's placeholder, §9's grant bullet), and the console
    handover protocol is §9a. The harness types four bytes AT the
    emulator's serial port, one at a time behind a delay so the driver must
    PARK — which is what puts the whole ladder under test rather than the
    echo alone — and they come back out echoed by a PROCESS, on riscv32 and
    arm64 both. The kernel's whole part is that it granted the window the
    image declared, routed the line, and woke the thread; none of those
    knows what a UART is. Two driver packages, one per DEVICE rather than
    per machine, because a driver IS its device.
    (e) **The native floor moved, and the reasons did not** (§5c has the
    arc). Every line M2 added is reason 1: one interrupt-class mask
    register on Profile A and four timer system registers on Profile B,
    `sos_syscall3` per profile (an op that answers with a value needs one
    and the C ABI declares no aggregate return), and `sos_wait_for_irq`
    per profile (`wfi` is an instruction). Assembly went DOWN, because a
    thread context built in Saw needs no register-clearing prologue.

## 12. The root server (ratified Aug 5, user)

- **Root = init + launcher + name service in ONE process, v1.** The
  decided model already pushes supervision, discovery, and policy to
  userspace; v1 collapses them into root rather than booting a service
  constellation. The DESIGN TEST: splitting the launcher or the name
  service into its own process later must require zero kernel changes —
  if the boot handle set can't support that split, the set is wrong.
- **Boot handle set** (minted by the kernel to root, everything else
  derives): the **System handle** (§2/§5.7 — debug_print/shutdown ops;
  ratified Aug 5); the LAUNCH capability (§7); root MemoryObjects — the
  RAM pool root and the device-range roots (§2.5); the root IRQ table
  (§2 Interrupt). Root's band map applies verbatim from its image (§7).
  **M2 ANSWERS THE LAST TWO WITH RIGHTS RATHER THAN OBJECTS**, and the
  set stays ONE handle wide: root's Process handle carries
  `BindInterrupt`, so binding a line is a derivation through a handle
  it already holds rather than a table it is given — and its device
  window arrives in its own image (§2.5), not in the boot set. Both
  become real objects in M3, and neither widens the register the kernel
  enters user mode with.
  NOTE (object-model brief material): the creation-authority model for
  plain objects (Channel/Event/Waiter/Timer) — quota-gated free
  creation vs a factory capability — is an open pin; M1 does not need
  it (root spawns nothing).
- **Loader-above-boot.** The kernel loads exactly ONE image ever: root
  (sosimg, §6). Every later process is loaded BY ROOT from images root
  obtains itself (e.g. a flash MemoryObject it holds), via LAUNCH +
  `create_process`. The kernel has no second code path for process two.
- **v1 protocol conventions** (userspace convention section, not kernel
  surface): a launched process receives ONE bootstrap channel handle at
  launch; its first messages request its initial handle set from the
  launcher (name-service discovery = ask by string name over that
  channel). Conventions are versioned in the `sos` userspace module,
  not in the kernel.
