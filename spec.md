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
  covers the gap). One optional arg on wait.

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

## 11. Remaining before the kernel briefs

- ~~Design (with user): root server responsibilities~~ RESOLVED §12
  (ratified Aug 5).
- **Orchestrator pins (veto-able), kernel-brief material:** rights-word
  bit assignments + per-object op tables (the syscall number space);
  kernel memory layout per profile (link scripts, static slab sizes,
  QEMU `virt` boot maps); where the physical-region refcount lives
  (§5.9); message limits (pinned 64-byte body / 4 handles) + concrete
  `sosimg` header incl. the §7 priority-map field; root server's
  bootstrap band map.
- **Roadmap (unchanged from §5b, brief numbers assigned at dispatch —
  the design-78/79 references there are stale):** M0 riscv32 QEMU
  target (design 112) → M1 riscv32 QEMU boot-to-root-server → M1b arm64
  EL1 parity + HAL extraction → the object model, landed once,
  two-profile-tested.
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
    keeps them all on the same trap path; `sos/rt/common/` (Saw) and
    `sos/rt/common_c/support.c` (the C that must stay C) are shared by the
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
