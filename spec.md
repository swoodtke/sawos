# SOS — a capability-based microkernel in Saw

Working notes. The name is SawOS ("SOS" for short — ruled Aug 28 2026, no
longer a placeholder). Requirements in
§1 are ratified (user, Jul 29); later sections are proposals and open
questions for discussion. First target: ESP32-P4 (riscv32) via Saw's
freestanding profile; the design must not preclude MMU-class targets.

## 1. Ratified requirements (user)

- **Capability-based microkernel.** The kernel implements only what is
  required to efficiently provide core OS resources:
  processes/address spaces, threads/tasks/scheduling, timers,
  interrupts, memory management. Everything else runs in userspace,
  with processes communicating via shared memory and pipes/events.
- **Handles.** Every kernel resource is referenced by a Handle: an
  opaque integer mapping to a kernel object through a per-process
  table lookup (fd-style). No ambient authority.
- **Derivation.** A Handle is ideally obtained via an operation on
  another Handle the process already holds, with the required
  capability enabled — authority flows only through held authority.
- **Pipes** send data synchronously, optionally carrying Handles,
  which are MOVED to the receiving process. Not every Handle is
  movable — transferability is a capability of the Handle itself.

## 2. Proposed object model (discussion)

Minimal kernel object types (each a slab-allocated kernel struct;
names provisional):

| Object | Role |
|---|---|
| `AddressSpace` | Isolation domain, defined abstractly. P4: PMP region set + APM/REE security context (see §5.5 — the P4's MMU is real but global/external-memory-only, not per-process). Paging targets: page-table root. |
| `Thread` | Kernel-scheduled execution context bound to an AddressSpace. Saw's cooperative TaskGroups run *inside* a thread, in userspace — the kernel never sees tasks. BUILT M2 (design 178 unit 2): ops `Start`/`Join`/`Exit`/`Yield`, rights `ThreadRight.Start`/`.Join`/`.Control`. The saved trap frame IS the context, so a switch is the trap handler returning a different frame — see §11. |
| `MemoryObject` | A range of memory (RAM or device MMIO) that can be mapped into AddressSpaces. Derived by splitting/attenuating a parent MemoryObject; roots handed to the first process at boot. BUILT M3 unit 2, FIRST SLICE (sawos design 2 D-2): a SEALED `{base, len}` and nothing else — no pools, no derivation, no `map()`, no attributes, no op table (an op aimed at one is a `BadOp` fault). Minted only at boot, one per row of the build-emitted REGION TABLE, delivered to root through `ProcessOp.BootHandleNext`, and NAMED as the two arguments of `process_create`. **BUILT OUT M3 unit 4 (sawos design 6), and the slice's two open questions are both answered**: the row is now `Memory` (RAM) or `IoMemory` (device MMIO) by the region table's KIND COLUMN, and the object has an op table. Ops `Split`/`Map`, rights `MemoryRight.Split`/`.Map` beside the universal pair (`Transfer` for unit 3's `give`, `Mint` for its attenuated sibling — the generic `Manage` that once sat here was removed by the Aug-29 doctrine, and unit 4's bits are named for their ops as that doctrine requires). `split(len)` is ONE CUT FROM THE FRONT and the parent becomes the remainder, so allocation is repeated front-splits and THE PARENT IS THE POOL CURSOR — arbitrary-offset carving is refused BY THE SHAPE rather than by a check, since one `{base, len}` slot cannot hold two remainders. `map(process, access)` installs a protection row and answers with a `Mapping`; it spends `MemoryRight.Map` on the region AND `ProcessRight.Map` on the target, because possession of bytes must not imply authority over an address space. There is STILL no op that reads a MemoryObject's bounds: a region is a capability, and what a process knows about where its memory is, it knows from the config that gave it the region. **FREE-ON-LAST-REFERENCE LANDED M3 unit 5** (sawos design 7 D-1), amending this row's M3-unit-4 "NOT built": a Memory slot carries a count of the handle entries naming it and returns to its slab inside the release that takes the count to zero. What is NARROWED rather than built is the BYTES — quotas count objects in v1, so a freed region returns its SLOT and no range to any pool (§2.5). **BUILT OUT AGAIN M3 unit 6 (sawos design 9 D-1): a fifth right, `MemoryRight.MapExecute`.** A `map` whose access word names `MapAccess.Execute` spends it, on top of the two above — so EXECUTABLE IS AN AUTHORITY rather than a free choice, and "only root maps executable" is a fact about capability FLOW (root never grants the bit at a mint) rather than about identity. `memory_rights()` mints it, of necessity: attenuation is monotonic, so a bit not minted at boot could never appear later, and the narrowing is a HOLDER's `MINT_OP` keep mask. Access is still per-MAPPING and a region still carries no R/W/X triple — what became a right is which access bits a HANDLE may request. Its companion is a `MapAccess` rule rather than a right: `Write | Execute` in ONE row is a caller-checkable `BadArg`, which costs nothing expressible because double-mapping is sanctioned (an RW row here and an RX row there is the same JIT-shaped pattern, with every individual row W^X). Both govern the DYNAMIC map only: an image's own X segments come through `process_create`'s loader under `SystemRight.ProcessCreate`. |
| `IoMemoryObject` | Physical memory-mapped DEVICE registers. BUILT M3 unit 4 (sawos design 6 D-1) as a DISTINCT KIND rather than a flag on `MemoryObject`, which is §2.5's pool ATTRIBUTE made a type: an IoMemory can only ever produce device-attribute rows — `map` takes no access argument at all — so a driver cannot obtain a cacheable view of a register block BY CONSTRUCTION rather than by a check. Its lifecycle differs everywhere too: PINNED (never freed — MMIO is not reclaimable), carved NON-EXCLUSIVELY (§2.5's "a fixed region may be handed out many times", so `carve(offset, len)` leaves the parent WHOLE where `split` consumes), and it has no contents. Ops `Carve`/`Map`, rights `IoMemoryRight.Carve`/`.Map` plus the universal pair. The machine's own granularity is checked AT THE CARVE against a per-profile HAL predicate (Profile A needs a naturally-aligned power of two — one protection entry; Profile B needs whole pages), because a window that could never be installed anywhere is a capability that lies about itself. One dispatch arm is the entire cost, and what it RETIRES is the M2 device-grant placeholder: see §2.5's migration case and §11. **UNTOUCHED BY M3 unit 6's exec gate, and that is a property of the kind rather than an omission** (sawos design 9 D-1): `IoMemoryOp.Map` takes no access argument at all, so execute-on-device is refused vocabulary and there is no `IoMemoryRight.MapExecute` to add. What unit 6 DID exercise here is its `Transfer` bit: root gives a driver child the console's window and the child maps it into itself, which is the flow this kind's `Transfer` was minted for at unit 4. |
| `Mapping` | ONE INSTALLED PROTECTION ROW, with its own handle. BUILT M3 unit 4 (sawos design 6 D-1/D-3). **A MAPPING IS AN INSTALLED GRANT ROW** — SOS does not translate (§5.5: an address is the same number in every process), so §2.5's "installed virtual placement" has no virtual half here and the object records which process's domain carries the row and which row it is. ONE op (`Unmap`, on `MappingRight.Unmap`), because everything else about a mapping was decided when it was installed. **RELEASING THE HANDLE IS NOT AN UNMAP**: release destroys the entry and never the object (design 3 D-2), so §2.5's "dropped without unmap = permanent, safe-but-leaked" falls out of existing doctrine rather than being new law. §2.5's sketched unmapping Deinit is deliberately NOT built — it would make the ROW's lifetime the wrapper's, and a launcher's wrapper drops right after it hands a child its memory. **THE LIVE-DOMAIN RULE** is the half a caller relies on: any edit to a grant record RELOADS IMMEDIATELY when that domain is the installed one, because `run_thread` skips equal domains and an unmap that waited for the next reschedule would be a revocation that did not revoke. Unmapping twice, or unmapping a Mapping whose TARGET PROCESS has died (its whole domain went with it), are both `BadState`. It is not givable — `mapping_rights()` withholds `Transfer` — because a Mapping names a row in one specific domain. |
| `Pipe` | Synchronous message IPC with request/reply built in — see §2.1 (ratified Jul 29; renamed from Channel + client API amended Aug 20). **BUILT M4 unit 1, A SLICE NAMED HONESTLY (sawos design 13): the CONNECTION as an object, with the data path and the lifetime machinery and no reply path at that unit — unit 2's block below is where the reply path landed.** What exists: two kinds, `PipeInlet` (the CLIENT end, requests flow IN) and `PipeOutlet` (the SERVER end, they come OUT), which is design 10 D-1's answer to the writer-count question — ROLES ARE OBJECTS. A single symmetric pipe object cannot express "the writers went to zero" (a parked receiver is itself a holder, the socket-`shutdown()` hole) and a rights-partitioned count on one object would be a second ledger against design 7's doctrine; a PAIR of counted endpoint kinds makes it the ledger that already exists. The two kinds TARGET ONE `PIPES` SLAB ROW with TWO reference columns, and which column a handle counts on is decided by its KIND — which §3's order establishes before any right is read. Factory: `ProcessOp.PipeCreate` on `ProcessRight.PipeCreate` (design 10 ruling 3 — pipes are IPC and carry nothing system-shaped, so the authority is per-process ATTENUABLE), answering BOTH ends through a two-word copy-out record because one op returns one word. Ops `PipeInletOp.Post` / `PipeOutletOp.Take` on `PipeInletRight.Post` / `PipeOutletRight.Take`, data-only, POLLING: a full ring and an empty one are both `SosStatus.WouldBlock`, never a park, because unit 1 has no waitability. Staging is a per-connection RING of `PIPE_INFLIGHT` slots at `PIPE_BODY_BYTES` each (design 10 ruling 6's amendment to §2.1's "one fixed slot" — still zero dynamic kernel allocation, which is the property that sentence existed for). **THE ZERO-ARMS ARE THE PEER-GONE DOCTRINE**: the outlet column reaching zero drops what is staged and makes every later `Post` answer `SosStatus.PeerClosed`, while the inlet column reaching zero DRAINS FIRST — `Take` hands over everything already staged and only a dry ring answers `PeerClosed`, terminal thereafter. Creator-pays: one `QuotaKind.Pipe` row charged at create, credited when BOTH columns reach zero and the slot goes back. Both ends carry `Transfer`, so a launcher wires them outward with `give` and a child drains one out of its boot set. **BUILT M4 UNIT 2, THE ONE-SHOT PAIR (sawos design 14): the REPLY PATH, so §2.1's request/reply primitive is a primitive here too.** Two more counted kinds, `PipeReply` (the client's claim on one reply) and `PipeRequest` (the server's obligation to send it), and D-1's answer to where they live is that a one-shot exists for exactly as long as one in-flight message — so they are RING-SLOT STATE, two more reference columns on the staging slot that carried the request and now carries the reply back (design 10 ruling 4's rider), with no third slab and no new quota row. `Post` grew its claim return and `Take` grew the obligation beside the message, which is §5.7's renumberable-op discipline doing exactly what unit 1 said it would. Ops `PipeReplyOp.Resolve` / `PipeRequestOp.Reply` on their own rights, both CONSUMING the caller's entry, so single use is enforced by the ledger and `NoCopy` is the ergonomics on top. **THE SLOT NOW LIVES UNTIL THE EXCHANGE SETTLES**, not until its bytes are taken, so `PIPE_INFLIGHT` bounds awaiting-reply exchanges — ruling 6's in-flight budget read literally. **ABANDONMENT IS DERIVED FROM THE COLUMNS**, not flagged: a dropped claim makes the server's `reply()` answer `PeerClosed` with the obligation discharged either way, a dropped obligation makes the client's `resolve` answer it, and a staged message whose outlet column reached zero is abandoned by a server that never existed. Transferable, so §2.1's zero-copy DELEGATION example runs end to end across a real process boundary. **BUILT M4 UNIT 3, WAITABILITY (sawos design 15): all four kinds are §2.2 waitables now, and the four `NotWaitable` refusal arms units 1 and 2 wrote by hand are gone.** No new object, no new op, no new slab — four kinds that already existed, each gaining a `Wait = 1 << 9` bit in its own rights enum and its own default set, and four columns in the per-kind matrix. The arms: the OUTLET is readable while a message is staged or the client end is gone (drain-then-terminal, so `PeerGone` waits for a dry ring); the INLET is postable while the ring has room or the server end is gone (design 10 ruling 8's addition to §2.2's list — `post` can never block, so a refused poster parks here); the REPLY is ready once its answer is written or can never be; the REQUEST is ready once the claim behind it was abandoned (§2.1's ratified early notice). **THE REPLY ARM IS THE ONE THAT CHANGED THE RECORD** (ruling 11(a)): its delivery carries the reply's BYTES, out of the ring slot that already holds them, so the wait IS the resolve and a multiplexed RPC is post + attach + wait. `WaiterOp.Add` grew a FLAGS word with it — `AttachMode.OneShot` detaches at delivery, and the CONSUMING attach (`Waiter.give`) releases the caller's entry so a multiplexer holds zero handles. With it the ratified blocking `send(msg)` / `send(msg, timeout:)` become real as LIBRARY compositions over post + attach + wait plus a Timer, which is what keeps the kernel from ever learning what a timeout is. **NOT BUILT, and each is a named later rung**: the FUSED fast paths `Call` / `ReplyRecv` (unit 3.5), and HANDLES IN MESSAGES (unit 4 — the bodies are bytes, so dropping a staged message is forgetting it). |
| `Event` | Accumulating non-blocking notification (OR / saturating-sum); a waitable — see §2.4 (ratified Jul 29). BUILT M2 (design 178 unit 3): ops `Signal`/`Receive`, the mode chosen by the caller at creation (`event_create(mode:)`). AMENDED Aug 17 (user): the word is CONSUMED BY WHOEVER TAKES IT, through either door — `receive` is the non-blocking poll, a `Waiter.wait` delivery is the blocking one, and both read-and-clear, so a value is reported exactly once (§2.2, §2.4). |
| `Clock` | A GRANTED TIME SOURCE — time is a capability, not an ambient facility. BUILT M3 (design 232 unit 1): obtained through `SystemOp.ClockGet` on `SystemRight.ClockGet`, ops `Now`/`TimerCreate`, rights `ClockRight.Read`/`.TimerCreate`. **A HARDWARE-BACKED CLOCK IS ONE KERNEL-ETERNAL OBJECT PER `ClockType`** (ruled Aug 17, user), existing from boot and owned by NOBODY: the machine has one monotonic counter, and a per-process object naming it would be a copy of a fact with a lifetime attached. So there is one slot per domain (the slot IS the domain's ordinal), no allocation and no `NoResource`, and process teardown frees no clock — a dead process's clock HANDLE is unbound like any other, and the object it named is not the process's to reclaim. `ClockGet` is therefore a GETTER that mints a handle onto a well-known object — and it MINTS ON EVERY ASK (sawos design 3 D-4, M3 unit 2.75), superseding this row's earlier "asking twice answers the SAME handle": two asks are two capability INSTANCES naming the one Clock, each independently owned and independently released, which is what §4's owning wrapper is an owner OF. It amplifies nothing (see §3's no-amplification amendment) and it makes the op fallible on repetition — a full handle table is `NoResource`, which is what earns it a quota row in unit 5. `ClockType` declares `Monotonic` ONLY in v1 (`Boot`/`Realtime` are future values of a raw-backed enum, undeclared because an unproducible case is dead surface), and `Now` dispatches on the clock's domain, so a second domain fails to compile until somebody says what its reading is. `Now` answers through a copy-out record, because a nanosecond count is 64 bits and one profile's registers are not. The point of the capability: strip the right from a child and hand it a VIRTUAL clock over IPC instead, with no code change on either side — and a virtual clock is a DIFFERENT animal, separately created and STATEFUL (offset, rate, owner), so it gets its own creation op and its own lifetime rather than a row in this table. |
| `Timer` | Deadline object bound to the Clock that created it; directly waitable. BUILT M3 (design 232 unit 1) — THE PROCESS-SLEEP PRIMITIVE, and before it a wait either returned at once or blocked forever. Ops `Arm`/`Disarm`, rights `TimerRight.Arm` (gating both) / `.Wait`. `arm(after_ns, interval_ns)` arrives through the new COPY-IN record (§2.2's copy-out funnel's mirror twin, built here and inherited by M4's IPC send) because two 64-bit times exceed the argument registers on a 32-bit profile; `interval_ns == 0` is a one-shot, which disarms itself when it fires. The re-arm is DRIFT-FREE (next = previous DEADLINE + interval, the timerfd model) and missed expiries COALESCE into a saturating fire count delivered as `WaitPayload.Timer(fires:)`. **There is NO ACK**: unlike §9's Interrupt there is no mask to release, so the wait that reports the fires is what consumes them. Arming an armed timer REPLACES its schedule and clears the count; disarming an unarmed one is a NO-OP, deliberately opposite to §9's ack-with-no-fire — a one-shot disarms itself, so cancelling a timeout that just expired is an ordinary race rather than a caller error. |
| `Interrupt` | Binds an IRQ line to a waitable; userspace drivers wait on it, ack via the handle. BUILT M2 (design 178 unit 4): one op (`Ack`), two rights (`InterruptRight.Wait`/`.Ack`), created by `ProcessOp.InterruptBind` on its own Process right — the factory bit a launcher strips from everything that is not a driver. The BINDING IS THE OBJECT'S EXISTENCE (creation takes the line, there is no rebind), which is what stops one handle naming two devices over its life. A line the board does not have, the TIMER's line, and a line already bound are all faults. |
| `Waiter` | Generic wait aggregator (epoll/Port-style) — see §2.2 (ratified Jul 29). BUILT M2 (design 178 unit 3): ops `Add`/`Remove`/`Wait`, rights `WaiterRight.Attach`/`.Wait`, the wait answer a copy-out record. AMENDED M4 unit 3 (sawos design 15): `Add` grew a FLAGS word — one-shot (detach at delivery) and CONSUMING (the op releases the caller's entry and the attachment owns the object) — and the wait RECORD grew a body region so a delivery may carry a message-sized payload, which one arm does. Neither cost a register or an op number; §5.7's renumberable discipline is what made both free. AMENDED M4 unit 0 (sawos design 12): its free has a SECOND last rite beside the attachment cascade — REVOCATION. Reaching zero references wakes every thread on its blocked list with `SosStatus.Revoked`, writing no record (nothing became ready, so there is no key, tag or payload), which supersedes M3's recorded "legal-but-doomed" strand. The count is the gate, not the release: a release with a minted sibling still live frees nothing and wakes nobody. It is the peer-gone doctrine's first instance — nothing parked can be silently doomed (§2.2). |
| ~~`MemoryObject`~~ | (A duplicate row from the Jul-29 draft, pointing at §2.3 where the section is §2.5. Both of its claims are in the rows above: RAM is `MemoryObject`, device MMIO is `IoMemoryObject`, and "mappable" is `Map`. Kept struck rather than deleted so a reader of the Jul-29 discussion finds where it went.) |
| ~~`Mapping`~~ | (Likewise — see the `Mapping` row above, built M3 unit 4.) |
| `Process` | AddressSpace + handle table + threads (ratified Jul 29: NO kernel Job/hierarchy). Kernel guarantees teardown on exit/fault — closing all handles, freeing/unmapping owned memory. Supervision (restart, kill-trees, launchd-style) is a USERSPACE concern. BUILT M2 (design 178 unit 2), one process: ops `ThreadCreate`/`ThreadSelf`/`Exit`/`GetStatus` plus the three factory ops `EventCreate`/`WaiterCreate`/`InterruptBind`, each on its own right. BUILT M3 unit 2 (sawos design 2), **TWO processes**: `Start` (on the CHILD's handle, `ProcessRight.Start`) mints the first thread of a created process and runs it; `BootHandleNext` (`ProcessRight.BootHandles`) drains §12's boot set one record at a time. The CREATE half of that lifecycle is not an op on this object — design 2's RIDER (Aug 29) puts `ProcessCreate` on System, because a process is a machine-wide resource (§12's amended creation-authority note). The child's handle carried `Start | Wait | Manage` and nothing else at that unit — everything a process may do to ITSELF withheld from its creator (M3 unit 3 re-ruled the set; see below). The TEARDOWN now forks (§8): process 0 stops the machine, any other reschedules. **THE SLOT OF A DEAD PROCESS IS RECLAIMED** (sawos design 3 D-3, M3 unit 2.75), and it is the ONLY slab that reclaims on a handle release. A `Gone` slot holds one thing — its §8 status word — and the only way to read that word is `GetStatus` through a Process handle, so "no handle names this slot" IS "no possible reader", exactly. The check therefore scans the handle tables (bounded: `MAX_PROCESSES` × `MAX_HANDLES`) when a released entry named a `Gone` process, and again at the end of a process's own teardown for its own slot. D-1's generations are what make the reuse safe, and `clear_domain` already invalidated `LAST_PROT_PROCESS` in anticipation. `MAX_PROCESSES` consequently bounds CONCURRENT processes again, which is what the name says. **SUPERSEDED IN ITS MECHANISM, NOT ITS ANSWER, BY M3 UNIT 5** (sawos design 7 D-1): the scan is a COUNT now — `ProcessSlot.refs`, maintained by the same lines that maintain an Event's — because unit 2.75's argument that a second fact would be one more thing to keep in step reverses once seven other kinds keep theirs at exactly those sites. The answer is identical, so no transcript moved for it. And this row is no longer the ONLY slab that reclaims on release: every countable kind does (see the counted-kinds column above), which is what makes a `Gone` process ordinary rather than special. `kill` (§8) still has no op. **BUILT M3 unit 3 (sawos design 4): `Give` — THE COURIER OP.** `give(handle, tag:)` on the CHILD's handle (gated by `ProcessRight.Give` there, plus the UNIVERSAL `Transfer` right on the handle being given) MOVES a handle into a fresh slot of the child's table and returns ONLY ITS STATUS: the child-side word is meaningless to the giver, which can call no op through it. What crosses instead is the TAG — the giver's own word, handed back unread at the child's drain. It is unbind-and-rebind with RIGHTS VERBATIM (a move, not a mint: no default set is consulted and nothing amplifies), the caller's entry unbinds exactly as a release does so the giver's word goes stale, and a full child table is `NoResource` with the give not having happened. Four caller errors END the caller: a handle that names nothing (`BadHandle`), one without `Transfer` (`AccessDenied`), a child that has already been STARTED (`BadState` — the boot set FREEZES at start, which is the launch flow's whole soundness argument), and a tag the child's set already carries (`DuplicateKey` — the tag is the identity, and one naming two handles would make the boot lookup ambiguous). `Start` gained a `boot_tag` argument in the same unit: the kernel resolves the tag to the child-side word, puts it in the child's first argument register and CONSUMES the record it named (the register IS the delivery, so a child can never be handed one word twice), leaving `_start(boot_handle)` unchanged and a launcher never seeing a child-relative word. `BootHandleNext` now drains the CALLER's own PER-PROCESS set — the kernel writes root's at boot and a launcher writes a child's with `give`, through one op with one exhaustion rule. The Process default set is now ONE set for all three minters (root's, `ProcessSelf`'s and `ProcessCreate`'s), named for its ops throughout: `ThreadCreate | ThreadSelf | Exit | Wait | EventCreate | WaiterCreate | InterruptBind | Start | BootHandles | Give` plus the universal `Transfer | Mint`. A LAUNCHER KEEPS the child's handle — it is what supervises with — and the child derives its own authority from the masked System handle it was given, so supervision and self-management are no longer alternatives (`MINT_OP`, §3, closing design 3's finding 2). **BUILT M3 unit 5.5 (sawos design 8): A PROCESS HANDLE IS A WAITABLE**, which is §8's own promise below and the fourth member of §2.2's list. Readiness is `state == Gone`; the payload is the §8 status word (`WaitTag.Process`, `WaitPayload.Process(status:)`); the right it spends is `ProcessRight.Wait`, the SAME bit `GetStatus` spends, because attaching is that question asked asynchronously and a second bit would let a supervisor poll a death it may not be woken by. It is TERMINAL LEVEL — the one readiness in the system a delivery does not consume — so a waiter attaching AFTER the death still wakes, a second wait answers the same word, and supervision has no lost-edge race. The attachment is a counted reference like every other waitable's, which is what keeps a dead child's slot readable for exactly as long as somebody is watching it. There is still no notification to anyone but a parked or attaching waiter: the Waiter IS the delivery system. |
| `System` | Kernel singleton (ratified Aug 5): the object behind system-scoped primitives so that EVERY syscall is an object op (§5.7) — v1 ops `debug_print`, `shutdown(status)` (stop the machine; QEMU: sifive_test), rights-gated (`SystemRight.Debug`/`.Shutdown`, §3 scoped rights). Root receives its handle at boot (§12). `exit` is NOT here — process exit belongs to the Process object when it exists (ratified Aug 5). M2 added a third op, `process_self` (design 178 unit 2): §3's derivation rule made real, so the boot register stays ONE handle wide and a process obtains its Process object THROUGH the System handle rather than being handed it. It was gated on the generic `Manage` until M3 unit 3 gave it `SystemRight.ProcessSelf` — a bit named for its op, per the Aug-29 doctrine, and a real attenuation seam: strip it and a child may print and tell the time and never learn its own identity. M3 added `clock_get` on `SystemRight.ClockGet` (design 232 unit 1: time is a granted capability) and, by sawos design 2's RIDER (Aug 29), `process_create` on `SystemRight.ProcessCreate` — the object's one FACTORY, here because a process is machine-wide and only this object is (§12's amended creation-authority note); `process_self` was already the precedent, since a Process handle has always come out of this object. **M3 unit 3 gave this object the launch flow's pivot** (sawos design 4, Aug-29 rulings): `root_system_rights()` gained `Transfer` — the M2 "nobody to transfer to" reason expired when unit 2 made a second process — so a launcher MINTS a masked sibling of its System handle (`MINT_OP`, §3) and GIVES that to a child. A child therefore bootstraps exactly as root does (§12's symmetry): System in the first argument register, its own Process handle derived from it, its boot set drained from there. `Debug` in the mask is what lets a child print without owning a device; `Shutdown` left out is what stops it halting the machine. Later candidates: info queries. |

**Thirteen of these kinds exist today** — System, Process, Thread, Event,
Waiter, Interrupt, Clock, Timer, MemoryObject (M3 unit 2), IoMemoryObject and
Mapping (M3 unit 4), since M4 unit 1 **PipeInlet and PipeOutlet**, and since M4
unit 2 **PipeReply and PipeRequest**
(`ObjType`, `kernel/abi/`, the kernel-internal numbering §5.7's vDSO discipline
keeps renumberable). **THE `Pipe` ROW IS NOW FOUR KINDS AND NOT ONE**, which is
design 10 D-1's ruling plus §2.1's own ratified pair rather than an
implementation choice: a connection is a PAIR of counted endpoint kinds and each
in-flight message is a second pair — the client's claim on a reply and the
server's obligation to send it — with "Pipe" surviving as the collective name,
and the row above says what the M4 slices built of it.

**AND TWELVE OF THE FIFTEEN ARE COUNTED** (sawos design 7 D-1, M3 unit 5). Every
countable kind's slab slot carries the number of handle entries naming it —
plus, for a waitable, its attachment — and reaching ZERO frees the slot
synchronously, inside the syscall that dropped the last reference. The column,
enumerated per kind so that a new kind fails to compile until somebody says what
references it:

| kind | counted references |
|---|---|
| Event | handle entries (any process) + its attachment |
| Waiter | handle entries |
| Interrupt | handle entries + its attachment |
| Timer | handle entries + its attachment |
| MemoryObject / IoMemoryObject | handle entries |
| Mapping | handle entries — **NOT its row**, which the Mapping owns rather than the reverse (§2.5) |
| Process | handle entries **+ its attachment** (M3 unit 5.5, sawos design 8 — a Process handle is a waitable now, and an attachment counts on every waitable); a LIVE process is never freed by losing its last handle, and a `Gone` one's slot is reclaimed exactly as design 3 D-3 ruled — this count is that ruling's handle-table scan, kept rather than recomputed. The attachment column is what makes attach-after-death SOUND against that reclaim: a watched dead slot cannot reach zero while somebody is watching it |
| PipeInlet | handle entries **+ its attachment** — **ON THE CONNECTION'S INLET COLUMN** (M4 unit 1, sawos design 13; the attachment column is M4 unit 3's, sawos design 15). The two endpoint kinds share one `PIPES` row and count on SEPARATE columns, which is what makes "every client handle is gone" askable at all; a side reaching zero raises PEER-GONE on the other, and the SLOT frees only when BOTH columns are zero. **THE ATTACHMENT IS PER END, not per connection**: a client waits for ROOM and a server waits for a MESSAGE, so §2.2's at-most-one-Waiter rule holds per column exactly as the count does |
| PipeOutlet | handle entries **+ its attachment** — the same row's OUTLET column, mirrored. The two arms of `free_object` are one act written twice, because reaching it through either kind means both columns are already zero |
| PipeReply | handle entries **+ its attachment** — **ON ONE RING SLOT'S CLAIM COLUMN** (M4 unit 2, sawos design 14; the attachment column is M4 unit 3's). **A DELIVERY OF THIS KIND DROPS THAT ATTACHMENT'S REFERENCE** (design 10 ruling 11(b)) — a claim is a one-shot, so its delivery is inherently consuming — which is the one place in the ledger where answering a wait is what frees an object. A one-shot exists for exactly as long as one in-flight message, so the pair is RING-SLOT STATE rather than a third slab: the object is an EXCHANGE (`connection * PIPE_INFLIGHT + ring slot`) and the claim counts on its reply column. The ring slot goes back when both of the exchange's columns are zero AND nothing is owed — a STAGED message is still owed to a live outlet whatever became of its claim, which is §2.1's TELL idiom |
| PipeRequest | handle entries **+ its attachment** — the same exchange's OBLIGATION column, mirrored. Reaching zero on ONE column is the ABANDONMENT transition, read off the counts by `reply` and `resolve` exactly as the connection's peer-gone is; and the CONNECTION cannot free while any exchange in it is live, which is what keeps a claim from being handed a recycled row |
| Thread | **NOT COUNTED IN v1** — the join/exit protocol owns a thread slot's lifetime on terms a handle count cannot express (`Exited` is a state a slot stays in so a late join still finds the exit code, and a joiner holds no handle). Recorded, deferred; the slot comes back at the teardown |
| Clock | **EXEMPT** — kernel-eternal, owned by nobody (the Aug-17 ruling). Freeing a domain's slot on a release would take the machine's counter from everybody else |
| System | no slab: the singleton every process's boot handle names |

The three kinds with LAST RITES are the three that hold something outside their
slab: an Interrupt masks its line (and is then bindable again, mid-life), a
Timer reprograms the comparator, and a Waiter detaches its list — which may take
a waitable to zero in turn, the one cascade, terminating because an attachment
references the WAITABLE and never the Waiter that holds it.

**AddressSpace is still implicit, and unit 4 is what makes the absence
deliberate rather than pending.** Each process gets one granted range plus a
writable window, and its protection rows are recorded in its process slot and
REPLAYED by the scheduler when it switches in (sawos design 2 D-5). That record
is what an AddressSpace object would own if one existed — and a `Mapping` is now
a handle onto ONE ROW OF IT, which is the shape that would have needed the
object most. It did not: `map` names a target PROCESS, because §5b says the
domain IS the process on both profiles, so an AddressSpace object would be a
second name for a thing every op already has. Nothing above it would change if
it existed. §11 is the ledger.

### 2.1 Pipes: bounded messages + built-in request/reply (ratified Jul 29;
### renamed from Channels + client API amended Aug 20)

- **`send(data?, handles?) -> PipeReplyHandle`.** Body bytes are copied
  from the sender's address space; handles are attached but TRANSFERRED
  into the receiver's table only when the receiver actually receives
  the message (rendezvous transfer — no orphaned handles if the send
  is abandoned). Both are OPTIONAL (data-only, handles-only, or both).
- **Bounded by design (kept simple on purpose):** a fixed maximum body
  size and a fixed maximum handle count per message → the kernel
  stages one message in a fixed slot, zero dynamic kernel allocation.
  Bulk data goes through shared memory (§2.5); the small message
  carries the MemoryObject handle. (Concrete limits: TBD — a small
  body, e.g. 64–256 bytes, and a handful of handles.)
- **Request/reply is a PRIMITIVE, not a convention.** `send` returns a
  **PipeReplyHandle** — a single-use, one-shot "reply pipe" the sender
  waits on. `receive(...) -> (message, PipeRequestHandle)` returns a
  matching one-shot **PipeRequestHandle** the server replies through
  (`request.reply(data?, handles?)`). Properties:
  - The reply capability is unforgeable and **consumed on reply**
    (NoCopy in Saw — the type system enforces single-use on top of the
    kernel's own check).
  - If the server drops its PipeRequestHandle without replying, the
    client's wait wakes with a **peer-closed error** — no hung
    clients, no timeout hacks. (seL4 call/reply + Zircon channel-call,
    fused.)
  - PipeReplyHandle/PipeRequestHandle **are transferable** (ratified Jul 29) —
    this is the delegation primitive, not a hazard. A server may MOVE
    its PipeRequestHandle to a third party who replies directly to the
    original client. **Canonical example — zero-copy delegation:** the
    filesystem receives a read (a PipeRequestHandle), forwards that handle
    over a pipe to the flash driver; the flash driver replies
    through it with the hardware bytes straight to the client — the
    payload never transits the fs address space. Single-use is
    preserved through transfer: the handle is consumed on reply
    regardless of who holds it (transfer moves the reply *obligation*).
  - **Fire-and-forget is NOT a pipe concern** — use an Event
    (§2.4), the accumulating non-blocking notification object. (Amended
    Aug 20: the TELL idiom below sanctions the payload-carrying one-way
    message; Event remains the payload-free door.)

**AMENDED Aug 20 (user) — the RENAME and the client API.** The object is a
**Pipe** (was Channel): the old name collided with Saw's own `Channel`
while sharing none of its semantics (buffered queue, any-holder close,
fire-and-forget — everything a rendezvous request/reply object is not),
and the planned Plan 9-style namespace resolves paths like `/dev/uart0` to
a server-owned object speaking a protocol, which is the NAMED-PIPE model
nearly verbatim (path-registered, message-framed, transactional). `Pipe`
names the PER-CLIENT connection object; the path-attached acceptor the
namespace will need is a separate, later object, named in the namespace
design. Handle types follow the abi's object-kind convention —
`PipeHandle`, `PipeRight`, and the auxiliary pair `PipeRequestHandle` /
`PipeReplyHandle` (the prefix also reserves bare Request/Reply for a
future protocol layer's own message types).

The client API (superseding Jul 31's separate `call` verb): ONE `send`
name, overloaded by the presence of `timeout:` — a runtime flag cannot
change a return type, so blocking-ness is never a parameter.
`pipe.send(msg) -> Result<PipeReply, PipeError>` suspends until the reply;
`pipe.send(msg, timeout: Duration) -> Result<PipeReply, PipeSendError>`,
where `case TimedOut(pending: PipeReplyHandle)` carries the still-live
claim in the error payload. `PipeReply` is the RESOLVED reply (body plus
transferred handles); a `PipeReplyHandle` resolves to one. The split-phase
form (send now, wait later; returns the bare `PipeReplyHandle`) is what a
client attaches to a Waiter to multiplex outstanding requests; its method
name is an M4-brief decision (leading candidate `post`).

**Abandoned requests** (a client drop and a client crash are the same
event to the kernel): `reply()` to an abandoned request answers
`Err(PeerClosed)`, never a silent success — ignorable with `let _ =`, the
std-Channel close precedent. The obligation is discharged and attached
handles closed either way. The `PipeRequestHandle` is WAITABLE for
"abandoned" (opt-in early notice; state on the kernel object, so it
travels with a delegated handle). Drop of a `PipeReplyHandle` is thereby
the CANCELLATION primitive — time out, then drop the pending handle; no
`cancel()` verb, and deterministic destruction makes the timing
deterministic. ABANDONMENT IS INFORMATION, NOT AN IMPERATIVE: whether it
cancels is the protocol's per-request-type decision (a tell-style message
ignores the signal; a cancellable read honors it). The TELL idiom is
sanctioned: send-then-drop carries data an Event cannot, the server
discharging with `let _ = request.reply()`; it is spelled on the
split-phase form, since the suspending `send` would wait for the very
reply it is about to discard.

**BUILT SO FAR — M4 units 1, 2 and 3: THE PAIR, THE ONE-SHOT PAIR AND
WAITABILITY (sawos designs 13, 14 and 15).** Nothing ratified above is reopened; what follows is a marker of
which sentences are executing and which are still promises, so a reader of this
section can tell the two apart without reading the tree.

- **EXECUTING.** The connection as an object, in the ROLE-SPLIT form design 10
  D-1 ruled: `PipeInlet` (client end) and `PipeOutlet` (server end), two
  counted kinds over one slab row with two reference columns. The amended
  "per-client connection object" sentence names the PAIR. The fixed body
  maximum is `PIPE_BODY_BYTES` (128, a build define inside this section's
  ratified 64–256 range) and the staging is a RING of `PIPE_INFLIGHT`
  (`2 × MAX_THREADS`) fixed slots — design 10 ruling 6's amendment to "stages
  one message in a fixed slot", which keeps the property that sentence exists
  for (zero dynamic kernel allocation) while letting a client post again before
  the server has taken the first. Body bytes are COPIED from the sender's
  address space through the §2.2 copy-in funnel, and a data-only message is the
  whole of what a message is today. The `data?` OPTIONALITY is executing on the
  data half: a zero-length body is legal and is a MESSAGE, distinguishable at
  the typed tier from "nothing staged".
- **THE PEER-CLOSED SENTENCES ARE EXECUTING, ON THE CONNECTION** rather than on
  the reply pair: `SosStatus.PeerClosed` answers a post whose outlet column
  reached zero and a take whose inlet column reached zero AND whose ring has
  been drained. Close-drains-first is the ruled reading — everything already
  staged is handed over before the server is told — and the level is TERMINAL,
  nothing un-closes.
- **THE ONE-SHOT REPLY PAIR IS EXECUTING** (M4 unit 2, sawos design 14), and it
  is the half of this section that had no implementation at all until it landed.
  `post` answers a **`PipeReplyHandle`** — the client's claim on one reply —
  and `receive` (the outlet's `Take`) answers the message together with the
  matching **`PipeRequestHandle`**, minted into the taker's table. Both are
  counted kernel objects and both are SINGLE-USE: `reply()` consumes the
  obligation and `resolve` consumes the claim, enforced by the ledger (the op
  destroys the caller's handle ENTRY) with `NoCopy` wrappers on top, so the
  type system's guarantee and the kernel's check are the same rule stated twice.
  Both are **TRANSFERABLE** — the ratified delegation primitive — and the
  zero-copy example is exercised end to end: a launcher takes an obligation off
  its own outlet, `give`s it to a child that holds no end of the connection, and
  the child's bytes land at the original claim.
  - **Where the pair LIVES is design 14 D-1**: a one-shot exists for exactly as
    long as one in-flight message, so the claim and the obligation are RING-SLOT
    STATE — two more reference columns beside the connection's two, on the
    staging slot that carried the request and that now carries the reply back
    (design 10 ruling 4's rider). There is no third slab and no new quota row.
    The consequence is visible: a ring slot lives until the exchange SETTLES
    rather than until its bytes are taken, so the fixed in-flight budget bounds
    AWAITING-REPLY EXCHANGES, which is what ruling 6's "in-flight messages per
    client" says read literally.
  - **ABANDONMENT IS EXECUTING, in both directions and derived from the
    columns.** Server drops its obligation -> the client's `resolve` answers
    `SosStatus.PeerClosed`; client drops its claim -> the server's `reply()`
    answers `Err(PeerClosed)` and THE OBLIGATION IS DISCHARGED EITHER WAY,
    ignorable with `let _ =`. A message still staged when the connection's
    server end goes is abandoned on the same terms, because its obligation can
    never be minted. Cancellation-by-drop and the TELL idiom (post, drop the
    claim, and the message is still delivered) both fall out of that and are
    exercised.
  - Resolving is NONBLOCKING, and it is now the POLLING door rather than the
    only one: pending is `WouldBlock` (`Ok(None)` at the typed tier) and
    consumes nothing. The blocking door is unit 3's, below.
- **BUILT M4 UNIT 3 — WAITABILITY, AND THE CLIENT API STOPS BEING A PROMISE**
  (sawos design 15; `designs/010` rulings 8, 9 and 11). All four pipe kinds are
  waitable now (§2.2's list closes over them), and three sentences of this
  section move from PROMISE to EXECUTING:
  - **THE SUSPENDING `send(msg)` AND `send(msg, timeout:)` ARE BUILT, AS LIBRARY
    COMPOSITIONS.** They are post + attach + wait, plus a Timer on the same
    Waiter for the timeout — ruling 4's shape exactly, so THE KERNEL STILL NEVER
    LEARNS WHAT A TIMEOUT IS: no duration argument, no timeout code in any pipe
    path. Backpressure has its leg too (ruling 8): a full ring parks on the
    inlet's room level instead of surfacing `WouldBlock`, which remains the
    honest answer for the caller that chose `post`.
    `case TimedOut(pending: PipeReplyHandle)` carries the STILL-LIVE claim
    exactly as this section ratified, and the composition therefore uses the
    NON-consuming attach — it has to be able to hand the claim back.
  - **THE `PipeRequestHandle`-IS-WAITABLE-FOR-ABANDONED SENTENCE IS EXECUTING**:
    the opt-in early notice is an attach on the obligation, delivered the moment
    the claim's reference column reaches zero, TERMINAL because a column that
    reached zero can never rise. A server that does not attach still learns the
    same fact the ratified way, at its own `reply()`.
  - **ABANDONMENT RE-WORDS TO THE LAST REFERENCE** (ruling 11, amending the
    "Abandoned requests" paragraph above). A handle drop with a LIVE ATTACHMENT
    is not abandonment — the subscription is the interested party, and the whole
    point of `Waiter.give` is that a client with an outstanding request holds no
    handle at all. What signals cancellation is the last reference going:
    removing the attachment, or the Waiter dying before delivery. So "drop of a
    `PipeReplyHandle` is the cancellation primitive" becomes "the last reference
    to it going is", and a client that gave its claim to a subscription cancels
    by unsubscribing. Everything downstream is unchanged: `reply()` to an
    abandoned request answers `Err(PeerClosed)`, obligation discharged either
    way.
  - **A DELIVERED REPLY DISCHARGES ITS EXCHANGE** (ruling 11(a)/(b)). The wait
    record carries the reply's bytes, so the wait IS the resolve; the ring slot
    goes back when the columns fall, and a claim the caller KEPT answers
    `PeerClosed` at a later `resolve` — there is no second copy of an answer.
    A one-shot kind's delivery detaches whatever mode was passed, because a
    subscription to something that has happened for the last time is a promise
    nothing can keep.
  - **STILL A PROMISE**: the FUSED fast paths `Call` and `ReplyRecv` (unit 3.5),
    and handles in messages with the completion-queue server (unit 4).
  - **A SPELLING NOTE, so a later reader is not surprised.** The tree renders a
    §2 object name short at its typed tier — `MemoryObject` is `Memory`,
    `PipeInlet`'s handle alias is `PipeInletHandle` — so `sos`'s owning wrappers
    for this pair are `PipeReply` and `PipeRequest`, with `PipeReplyHandle` /
    `PipeRequestHandle` as the `sosabi` aliases those wrap. That takes the name
    the client-API paragraph above uses for the RESOLVED reply value, which is
    still a promise (unit 3's suspending `send` is its only producer); the unit
    that builds it names it, and this paragraph is the notice that it must.
- **PROMISES STILL.** The `handles?` half of a message and rendezvous handle
  transfer (unit 4 — which is why "no orphaned handles if the send is abandoned"
  has nothing to be true of yet), and the FUSED fast paths `Call` / `ReplyRecv`
  (unit 3.5). The one-way `post` the client API paragraph named
  as a leading candidate IS the kernel's one submission op — ruling 4 promoted
  it from a spelling to THE primitive, with blocking send as a library
  composition over it, and M4 unit 3 is where that composition became real.
  **`PipeReply` IS STILL THE WRAPPER NAME AND NOT THE RESOLVED VALUE'S**: the
  client-API paragraph's `PipeReply`-as-resolved-reply is `ReplyDelivery` in the
  built surface — `Data(msg: PipeMsg)` or `PeerClosed` — because the short name
  was already taken by the claim's own wrapper (unit 2's spelling note predicted
  this and asked the unit that built the value to name it).

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
  is universal and the PAYLOAD is not — a Pipe's readiness is not a
  Timer's is not an Event's — so a fixed register pair would have had to
  be the union of every waitable's answer forever, and the register file
  is the one thing that cannot grow. The Event's payload is its
  accumulated word, which makes the common wait one syscall instead of
  two.
- **THE ANSWER CONSUMES WHAT IT REPORTS** (ruled Aug 17, user, amending
  the Aug-16 snapshot wording above). A delivery of an Event READS AND
  CLEARS the word into the wait record, in one interrupts-masked step —
  so the bits a record carries are bits nothing else will ever be told
  about, and a `receive` right after a wake answers zero. A signal
  landing after the take re-arms readiness with its own bits alone:
  nothing lost, nothing double-counted. The alternative — a snapshot the
  wait leaves behind — reports the same bits to the next wait as well,
  and a recipient cannot tell that duplicate from a real second signal.
  §2.4's `receive` keeps its exact semantics as the NON-BLOCKING POLL;
  the two are doors onto one value, and a process picks by whether it
  wants to park.
- **THE MULTIPLEXING RULE, in one line:** many threads per Waiter is
  DISTRIBUTION (one wake per delivery, the worker-pool shape); one
  Waiter per waitable is OWNERSHIP, and it is structural — a handle
  attaches to at most one Waiter, so a delivered value has exactly one
  recipient by construction and no multi-watcher caveat exists.
- **THE WAITER ITSELF CAN GO, AND A PARKED THREAD IS TOLD** (BUILT M4
  unit 0, sawos design 12; `designs/010` rulings 1 and 2). When a
  Waiter's LAST reference drops, its free wakes every thread on its
  blocked list with `SosStatus.Revoked`. Three things make that one
  sentence:
  - **NO RECORD IS WRITTEN, and that is the contrast to hold onto.** A
    delivery copies a record into the caller's memory and THEN answers
    `Ok`, because something became ready and the record is what became
    ready. Nothing became ready here — no key, since a key names an
    attachment; no tag, since no waitable fired; no payload, since there
    is no value — so a revocation writes the STATUS REGISTER and nothing
    else, and the woken `wait` returns with its buffer exactly as it left
    it. Said as the pair: a delivery copies out and answers `Ok`; a
    revocation answers `Revoked` and copies nothing.
  - **THE WALK IS THE WHOLE LIST, not the pop a readiness does.** The
    distribution rule above is about a ready HANDLE, of which there is
    one; here the thing every parked thread is waiting on is what ceased
    to exist, so every one of them is answered.
  - **THE COUNT IS THE GATE, NOT THE RELEASE.** A release with a minted
    sibling still live frees nothing and wakes nobody — that is the
    §2 reference ledger unchanged, and it is the difference between
    revocation and any-release.
  It supersedes M3's recorded "legal-but-doomed" stance, under which such
  a thread stayed `Blocked` forever for the deadlock report to name
  (design 7's As-built finding 2, which carries the rider). The husk
  alternative — count a parked thread as a reference so the Waiter never
  reaches zero — was weighed and REJECTED: it keeps the thread alive and
  makes the drop silent, turning an error a program can handle into a
  hang somebody has to infer. **THIS IS THE PEER-GONE DOCTRINE'S FIRST
  INSTANCE, and the doctrine is the contract: NOTHING PARKED CAN BE
  SILENTLY DOOMED** — every "what you are waiting for can no longer
  happen" is a delivered wake carrying a distinguishable status, never a
  strand. Its other two instances are M4's and are specified in
  `designs/010`: the ratified one-shot reply pair (§2.1's `PeerClosed`,
  both directions) and a connection endpoint reaching zero references
  (that sketch's D-1). Terminal levels follow design 8's precedent —
  attaching after the fact still wakes, and nothing un-closes. The
  deadlock predicate gained no arm: the wake is synchronous inside the
  syscall that dropped the last reference, so there is no idle-and-doomed
  state for it to see.
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
- Waitables: Pipe (readable / **room-to-post**), Event, Timer,
  Interrupt, **Process** (§8), PipeReplyHandle (reply-ready), and
  PipeRequestHandle
  (abandoned — the Aug 20
  amendment, §2.1). **ALL EIGHT ARE BUILT** (M2 units
  3 and 4; design 232 unit 1; sawos design 8, M3 unit 5.5, for the fourth; sawos
  design 15, M4 unit 3, for the four pipe arms). **THE LIST CLOSES HERE**, and
  the four that closed it took no new object, no new op and no new slab: they are
  kinds that already existed moving out of the `NotWaitable` refusal arms units 1
  and 2 wrote by hand, each spending a `Wait` bit added to its own rights enum
  and its own default set. **THE CLIENT END JOINED THE LIST** (`designs/010`
  ruling 8, amending this line): `post` can never block, so a refused poster
  needed something better than a poll loop, and the inlet's ROOM-TO-POST level is
  it — ready while the staging ring has a free slot OR the server end is gone,
  with the payload word saying which, so a waker learns whether to post or to
  stop without a probe post. It is a plain LEVEL and not a terminal one: consume
  clears nothing, and the level's end is the poster's own next post. A waitable
  THREAD is still
  §8's deferred half, and attaching one is a `NotWaitable` fault for a different
  reason. The second kind is what moved an
  attachment
  out of the waitable and into a table of its own: a Waiter's set has to
  be ONE list, since a wait scans it once and a `remove` walks it once,
  so per-kind lists would make both a matrix over kinds. What stays
  per-kind is FIVE questions — who is watching me, set who is watching
  me, am I ready, what does a record say, and (added by the Timer) a
  record was DELIVERED — each an exhaustive match, so a further waitable
  cannot be added silently. **THAT PROPERTY WAS COLLECTED RATHER THAN
  CLAIMED when the fourth kind arrived** (sawos design 8): adding
  `WaitableKind.Process` failed all five matrix arms, adding the `WaitTag`
  and `WaitPayload` cases failed every userspace match on a wait answer,
  and `waitable_slot` failed its own — so the compiler enumerated the
  work instead of a reviewer having to.
  The fifth question is the ACK-FREE DRAIN, and the four built kinds
  answer it three different ways: an Event's word and a Timer's
  fire count are both SPENT by the delivery; an INTERRUPT
  answers with nothing, because its readiness ends at the driver's ack —
  the line stays masked until the device has actually been serviced, so
  the kernel cannot know a record was enough; and a PROCESS's death also
  spends nothing, for the OPPOSITE reason — it is the TERMINAL LEVEL, so
  there is nothing to end. Nothing un-dies: a delivery reports the §8
  status word and leaves it, so every later wait on that attachment
  answers the same word, and a waiter that attaches after the death is
  told at once. Delivery is one funnel, so
  the drain cannot happen on the already-ready path and not on the wake
  path.
  **AND THE FOUR PIPE ARMS ANSWERED THE FIFTH QUESTION THREE MORE WAYS** (sawos
  design 15). A READABLE outlet and a POSTABLE inlet spend nothing, on the
  Interrupt's terms: their readiness has an end the kernel does not own — a take,
  and the poster's own next post. An ABANDONED claim spends nothing on the
  PROCESS's terms: nothing un-abandons, so there is nothing to end. The REPLY is
  the fourth answer and the only one that is neither: its delivery DISCHARGES
  the exchange, because the record carried the reply itself and a second copy of
  an answer must not exist. So the five kinds that answer "nothing" now do it for
  four distinct reasons, which is what the matrix is a matrix for.
- **THE COPY-IN DOOR is the copy-out funnel's mirror twin** (design 232
  unit 1), built for `Timer.Arm` and inherited by §2.1's message body. It
  refuses the same two things — a misaligned address, an address outside
  the process — and TERMINATES the process for either, on the same
  reasoning. One asymmetry is deliberate: copy-out's window is the
  process's WRITABLE memory and copy-in's is the whole GRANTED region,
  because a record may legitimately come out of a process's own rodata,
  and the two doors guard different hazards (writing outside the writable
  window corrupts the process; reading outside the granted region reads
  somebody else). A copy-in LENGTH is checked for equality rather than
  "at least": a capacity may be over-provisioned, but an inbound length is
  the caller's statement of what it is handing over.
  **Attach semantics (ratified Jul 29, amended M4 unit 3):**
  **level-triggered** (keeps reporting ready until the waiter handles
  it — no lost edges); attachments are **persistent BY DEFAULT** with explicit
  `remove(key)` (and the KEY, per the Aug-16 amendment
  above, never the waitable's handle); a handle attaches to **at most
  one Waiter**, but **multiple threads/tasks may wait on the same
  Waiter** (a ready handle wakes one waiter — the shared
  readiness-queue shape).

- **ONE-SHOT IS AN OPT-IN PROPERTY OF THE ATTACHMENT** (BUILT M4 unit 3, sawos
  design 15; `designs/010` ruling 9, amending "no one-shot mode" above).
  `AttachMode.OneShot` detaches AT DELIVERY — when the record for its key is
  COPIED OUT, never at mere readiness, and the delivery is otherwise identical.
  It is the attacher's choice like the key, spelled as a defaulted trailing
  parameter so no existing call site changed. What it buys: a blocked poster's
  room-wait, a request-abandoned watcher and a wait-once death supervisor all
  stop owing a trailing `remove`. **THE FOOTGUN IS STATED RATHER THAN GUARDED**:
  attach one-shot, take the delivery, and wait again on a Waiter with nothing
  left attached, and the thread parks forever — which the deadlock report names,
  exactly as it always has for an empty Waiter.
  **AND AN OP THAT CONSUMES ITS OBJECT DETACHES IT IMPLICITLY** (ruling 9(a),
  MANDATORY): `PipeReplyOp.Resolve` and `PipeRequestOp.Reply` destroy the
  caller's entry, and an attachment is a counted reference, so without the
  implicit detach the op could not free what it consumed and refusing while
  attached would wedge the op ordering instead.

- **THE CONSUMING ATTACH IS A DIFFERENT VERB, NOT A THIRD MODE** (BUILT M4 unit
  3; ruling 11(c)). `Waiter.give(...)` takes the wrapper BY MOVE: the op
  releases the caller's handle entry and the ATTACHMENT owns the object from
  there, so a multiplexer holds ZERO handles however many things it is watching
  and "hanging up" is destroying the subscription. It is a verb rather than a
  mode value because consuming is an EFFECT change and a runtime flag may not
  make one — §2.1's own reason for rejecting a send-mode enum. The two axes are
  ORTHOGONAL: `add`/`give` crossed with `Persistent`/`OneShot` is a 2x2. A
  refused `give` HANDS THE OBJECT BACK in its error payload, because a full
  attachment table is a resource condition and the caller keeps what it had.

- **A DELIVERY MAY CARRY A PAYLOAD LARGER THAN A WORD, AND THE RECORD GREW TO
  HOLD ONE** (BUILT M4 unit 3; ruling 11(a), amending the record shape above).
  The record is now a four-word HEADER — key, tag, payload, BODY LENGTH — followed
  by a BODY REGION of `PIPE_BODY_BYTES`, and the published capacity a caller must
  supply is the two together. Its one producer today is a reply-ready delivery,
  which hands over the reply's own bytes out of the ring slot that already holds
  them: the wait IS the resolve, so a client that waits this way never calls
  `resolve` at all and a multiplexed RPC is post + attach + wait.
  **THE BUFFER IS MAX-SIZED AND THE COPY IS NOT** (ruled Sep 1, user): a parked
  waiter cannot predict which arm fires, so the buffer holds the biggest answer
  its attachments could give, while the kernel copies only what the delivery
  needs — four header words for a word-payload kind, four words plus `body_len`
  for a reply with data. So the growth costs a Timer waiter buffer SPACE and
  never copy TIME. `wait_record_bytes()` moved, which the vDSO discipline makes
  free: every typed caller's buffer comes out of the wrapper's own frame, so the
  number is written once and read once.

### 2.4 Event: accumulating non-blocking notifications (ratified Jul 29)

- The fire-and-forget primitive. `event.signal(bits)` NEVER blocks;
  the kernel accumulates into the Event's word. `event.receive() ->
  word` drains and resets to 0. Waitable (non-zero = ready → attach to
  a Waiter).
- **THE WORD HAS TWO DOORS AND BOTH CONSUME IT** (ruled Aug 17, user).
  `receive` is the NON-BLOCKING POLL — drain-and-reset, 0 when empty,
  never parks — and a `Waiter.wait` delivery is the blocking one, whose
  record carries the same accumulated word and clears it in the same
  interrupts-masked step (§2.2). A value is therefore delivered exactly
  once, whichever door it came through, and the choice between them is
  only whether the caller wants to park. The recipient is unique by
  construction: a handle attaches to at most one Waiter, so no second
  watcher can be deprived of a word a wait took.
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

**BUILT M3 unit 2 (the first slice), M3 unit 4 (the rest of it), AND M3 units 5
and 6 (the refcount, shared memory, and executable-as-a-right)** — sawos designs
2 D-2, 6, 7 and 9. Everything this section describes exists now, the refcount
included; what is NARROWED rather than missing is stated below in the clause's
own words.

**THE NAMES AS BUILT, stated once because this section predates them.** The
Jul-29 draft wrote `MemoryObject`, and this document still does wherever it
quotes itself; the kinds the kernel declares are **`Memory`** (RAM),
**`IoMemory`** (device MMIO) and **`Mapping`** (one installed row) — `ObjType`
9, 10 and 11 in `kernel/abi/`, the numbering §5.7's vDSO discipline keeps
renumberable. The verbs are `split` (RAM: one cut from the front, the parent
becomes the remainder), `carve` (device: the parent stays whole), `map` (install
a row, answer a `Mapping`) and `give` (§3's move of a region into a child's
table). The longer Jul-29 spellings name the same three kinds wherever they
survive above and below; nothing was renumbered and no wrapper changed.

**WHAT UNIT 2 BUILT** was deliberately the smallest useful part: a MemoryObject
was a SEALED `{base, len}` — no pool, no derivation, no `map()`, no attribute,
no ops — minted only at boot, one per row of a build-emitted region table, its
whole surface being NAMED as an argument to `process_create`. Those were exactly
this section's "**pool roots given to the root server at boot**" in a v1 static
form. The one thing the slice settled is the one thing unit 4 kept: a region is
a CAPABILITY rather than a description, so there is STILL no op that reads a
MemoryObject's bounds and a process that holds one can hand it to the kernel and
cannot learn a number from it. What a process knows about where its memory IS,
it knows from the config that gave it the region — which is what root's
tag-to-meaning config has always been, and where the uart-echo driver's
`UART_BASE` has always come from.

**WHAT UNIT 4 BUILT, point by point against the bullets below** (sawos design 6):

- **THE POOL ATTRIBUTE BECAME A KIND.** A RAM pool root is a `MemoryObject` and
  a device pool root is an `IoMemoryObject`, decided by a KIND COLUMN in the
  build-emitted region table (design 6 D-5, table version 2). Design 46's intent
  marker is therefore not a flag anybody checks: an IoMemory's `map` takes no
  access argument and installs a device-attribute row, and there is no other op
  on the kind, so "a Device MemoryObject can only produce Device mappings"
  holds BY CONSTRUCTION. The attribute travels with the handle because the
  attribute IS the handle's kind.
- **UNIQUE-VS-SHARED ALLOCATION BECAME TWO VERBS.** `MemoryObject.split(len)` is
  ONE CUT FROM THE FRONT and the parent becomes the remainder — allocation is
  repeated front-splits and the parent IS the pool cursor, so a piece is
  allocated once and unavailable until freed. `IoMemoryObject.carve(offset, len)`
  leaves the parent WHOLE, which is this section's "a fixed region may be handed
  out many times", and the window is never freed because MMIO is pinned.
- **`map(aspace, ...) -> Mapping` IS BUILT, and `aspace` is a PROCESS.** §5b
  says the domain IS the process on both profiles, so the argument names the
  process rather than an object that would be a second name for it. It answers
  with a distinct kernel object with its own handle, and ONLY THE MAPPING HANDLE
  CAN UNMAP.
- **THE DELIBERATE STANCE HOLDS AND IS NOW FREE.** Dropping a Mapping handle
  without unmapping means that mapping is permanent — safe-but-leaked, never a
  dangling-unmap hazard — and it costs no new rule: release destroys a TABLE
  ENTRY and never an object (design 3 D-2), so the stance falls out of doctrine
  that predates it. **The Deinit-unmaps-at-scope-exit half is NOT built**, and
  the reason is the shape unit 4 exists for: a launcher that maps a page into a
  child holds the Mapping in a local, and a scope-exit unmap would revoke the
  page before the child ever ran. Tying the row's lifetime to a wrapper looked
  like it wanted the refcount immediately below; **unit 5 landed that refcount
  and RULED THE OTHER WAY** (D-2, two paragraphs down — a Mapping owns its row
  and the row is not a reference back), so the Deinit stays DECLINED on the
  launcher argument rather than pending on a mechanism.
- **THE REFCOUNT INVARIANT WAS NOT BUILT AT UNIT 4, AND THE CLAUSE IS QUOTED
  HERE SO THE DEFERRAL — AND THEN ITS NARROWING — STAYS
  VISIBLE**: "*a Mapping holds a reference to the physical pages (not
  merely to a MemoryObject handle). The pages are freed only when the LAST
  reference of either kind — any MemoryObject handle OR any Mapping — drops.*"
  Nothing in unit 4 frees pages at all: a split is PERMANENT until its owner's
  teardown, and teardown returns slab SLOTS rather than ranges, so a pool walked
  to its end stays walked and `NoResource` is what says so (the `memory_split`
  case asserts exactly that). Free-on-last-reference lands with unit 5, beside
  quotas, which is where a refcount has something to be checked against.

  **BUILT M3 UNIT 5 (sawos design 7 D-1), AND NARROWED WHERE IT LANDED.** The
  refcount exists: every countable slab slot carries one, the count reaches zero
  inside the syscall that drops the last reference, and the free is synchronous
  — no deferred reclamation, no cleanup queue. `memory_split`'s claim flipped
  with it, from "the slab runs out" to twenty cut-and-drop rounds past a slab of
  sixteen. **What the clause above says about PAGES is deliberately still not
  true, and the narrowing is the ruling rather than an omission: QUOTAS COUNT
  OBJECTS, NOT BYTES, in v1.** A freed Memory slot returns to its slab and its
  bytes return to NO POOL — a front-cut parent is a one-way cursor and cannot
  absorb an arbitrary hole, which is the one-`{base, len}` shape design 6 D-2
  chose — so what unit 5 reclaims is the SLOT, not the range. Byte accounting
  and pool returns arrive with a real allocator (M4+); until then a program that
  spends a pool has spent it, and the clause above is quoted here with this
  paragraph beside it so the remaining gap stays visible rather than being
  read as done.

  **AND ONE MAPPING SHAPE IS RULED HERE RATHER THAN INFERRED** (design 7 D-2).
  A Mapping OWNS its row; the row is not a counted reference back. So a
  mapped-but-unreferenced Mapping — every handle released, the grant still
  installed — reaches zero, FREES ITS SLOT AND LEAVES THE ROW: exactly this
  section's "dropped without unmap = permanent, safe-but-leaked", now a line of
  kernel rather than a sentence about one. The row is then unremovable until the
  TARGET process's teardown, which is what "leaked" has always meant and is the
  bound on it. `mapping_slot_free`'s husk arm writes and reads through such a
  row to show it. The mirror case is a husk of the other kind — unmapped but
  still referenced — whose `Unmap` stays the `BadState` fault it already was.

**THE MIGRATION CASE BELOW CAME TRUE** (design 6 D-6). M2's boot-time device
grant was a `sosimg` record a driver package declared in its manifest; both
driver packages now receive the console's register page as an `IoMemory`
capability in their boot set and map it into themselves, and the echoed bytes
and the line number in their transcripts are UNCHANGED. That is the migration's
own claim — "the same window, obtained rather than declared" — as a diff of two
console transcripts. What has NOT retired is the `SegFlag.Device` flag itself
and the boot loader's `allow_device: true` door: the emitter is sawlang-side
(`blade/sosimg.saw`), so deleting the flag is a pin-bump event. The path is
DORMANT — no image in this tree uses it — and its retirement is a backlog entry.
Children still cannot declare windows (`allow_device: false`, unchanged); a
driver child gets its window because root MAPS it in or GIVES it a carved
IoMemory, which is unit 6's flow.

**AND UNIT 6 RAN THAT FLOW** (sawos design 9 D-2). Root drains the console's
register page out of its own boot set, `give`s the `IoMemory` to a child under a
tag, and the CHILD maps it into itself and echoes the harness's bytes — on both
profiles, with the driver body byte-identical to the root-as-driver twin's apart
from the three things being a child changes (the window arrives by give rather
than as a region row, it ends with `Process.exit` because it holds no
`SystemRight.Shutdown`, and it is linked at the child base). The two twins STAY
in the suite unmoved, so the same driver running as root and as a child is two
transcripts side by side. `give(iomemory:)` is the one piece of surface it
needed: `iomemory_rights()` has minted `Transfer` and the kernel's `boot_kind_of`
has had its `IoMemory` arm since unit 4, both written for exactly this.

**AND THE OUTER BOUND ON ALL OF IT: A DMA-CAPABLE DEVICE'S DRIVER IS INSIDE THE
TCB** (sawlang#178 round 4, ruled Aug 16; recorded here because this is where
device grants are ruled, and stated in M3's docs sweep because M3 is the
milestone that made a driver a separate process). A `map` bounds what a driver's
CPU may reach. It bounds nothing a BUS MASTER may reach — so a driver that can
program a DMA engine can make that engine write any physical address on the
machine, and the window it was granted is a fence around the DRIVER, never
around the DEVICE. Until an IOMMU exists, such a driver is a TCB member and is
trusted like one; isolating it is not something a rights mask can do. The ruled
architecture is a USERSPACE IOMMU DRIVER that owns the IOMMU as an ordinary
device (a window and a line, granted by root) and to which a DMA driver SENDS
Memory handles over IPC, creator-pays charging composing unchanged. It names two
prerequisites and M3 landed the first: **process-death notifications** (§8, unit
5.5), so the IOMMU driver can stop a dead process's device operations and
release its memory deliberately rather than by teardown. The second is
**critical processes** — a process root marks at launch whose exit FOR ANY
REASON faults the system, checked at the TOP of teardown ahead of any release,
because the halt must preempt the free or the window it exists to close reopens
inside it — and it is unbuilt, M4+, filed with the IOMMU item. None of this
costs the current tree anything, which is what keeps M2/M3's scope honest: both
v1 boards' consoles are programmed-I/O, so no case in the suite is a DMA driver
and no capability in the tree is a lie about one.

**AND THE SHARED-MEMORY BULLET BELOW CAME TRUE WITH IT** (design 9 D-3): one
region, `map`ped into two processes, written from each side and read from the
other. The launcher installs the CHILD's row itself, so that child holds no
`Memory` handle at all — access without possession — which is the deliberate
contrast with the driver child, which was given the capability and installed its
own row. Two installation directions, one unit, no mode flag anywhere.

**EXECUTABLE BECAME A RIGHT** (design 9 D-1, ruled Aug 30), which is this
section's `map(aspace, ...)` amended in one place and left alone everywhere else:

- **ACCESS IS STILL A PROPERTY OF THE MAPPING.** A region carries no R/W/X
  triple and nothing about double-mapping one region RO here and RW there
  changed. What became an authority is WHICH ACCESS BITS A HANDLE MAY REQUEST:
  `MemoryOp.Map` refuses `MapAccess.Execute` unless the Memory handle carries
  **`MemoryRight.MapExecute`**, the same refusal (`AccessDenied`, a fault) every
  unspent right answers. It is CAPABILITY FLOW, not identity — "only root maps
  executable" holds because root never grants the bit, not because the kernel
  asks who is calling — and `memory_rights()` therefore MINTS it at boot, of
  necessity: §3's attenuation is monotonic, so a bit not minted at boot could
  never appear later, and the narrowing is a holder's `MINT_OP` keep mask.
- **NO ROW IS BOTH WRITABLE AND EXECUTABLE.** `Write | Execute` in one access
  word is a caller-checkable `BadArg` fault, landing beside the existing
  write-without-read refusal. It costs nothing expressible, because the double
  map is already sanctioned: an RW row here and an RX row there over one region
  is the JIT-shaped pattern in full, and what is removed is only the single row
  a process could write and then fetch from.
- **THE BOUNDARY IS THE DYNAMIC MAP.** Neither rule governs a process image's
  own executable segments: those arrive through `process_create`'s loader as
  `SegFlag` bits read out of the image, validated by `imgformat.has_sane_perms`
  (which refuses write-without-read and X-on-device and deliberately not W|X —
  an image's segments are kept apart by its linker script), under
  `SystemRight.ProcessCreate` authority that a driver child does not hold.
- **STATED HONESTLY: IT IS PER HANDLE, NOT PER REGION.** A second handle that
  still carries the bit can map the same bytes executable. Design 6's aliasing
  stance is unchanged, and a region-level immutable flag was REJECTED as a second
  mechanism doing overlapping work. `IoMemory` is untouched throughout —
  execute-on-device was already refused vocabulary and its `map` takes no access
  argument at all.

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
    says so where the check is. **DONE M3 unit 4** (sawos design 6 D-6):
    both driver packages migrated, same echoed bytes, same line number.
- Sendable over pipes — so the SAME physical memory can be mapped
  into multiple address spaces at multiple virtual locations (the
  shared-memory primitive). Derived by splitting / attenuating a
  parent; pool roots given to the root server at boot.
  **THE SHARED-MEMORY HALF IS BUILT (M3 unit 6, sawos design 9 D-3)** — one
  region mapped into two processes, written from each side and read from
  the other (`share_double_map`) — and NOT "at multiple virtual
  locations", which SOS does not have: §5.5 says an address is the same
  number in every process, so a region is shared AT ITS OWN ADDRESS. The
  SENDABLE-OVER-PIPES half is M4's, because pipes are; until then a region
  reaches a second process through `give` (before its start) or through a
  row the launcher installs (any time). Splitting and attenuating are both
  built — `MemoryOp.Split` and the universal `MINT_OP` over
  `MemoryRight` — and unit 6 is the first unit with a reason to attenuate
  a REGION rather than merely split one.
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
  **GENERATIONS ARE BUILT** (sawos design 3 D-1, M3 unit 2.75). An entry
  is (object type, rights, target, generation), and a handle word is
  `(generation << HANDLE_INDEX_BITS) | (index + 1)` — the index field's
  width is ONE NAMED CONSTANT in `sosabi` (default 8) with every mask and
  shift derived from it, and the remaining bits of a portable 32-bit
  budget are generation, identically on both profiles. Four properties
  worth stating here:
  - **Generation 0 stamps nothing.** A slot's first-life word IS the bare
    1-based index, so §12's boot order and every contract written before
    generations existed survive verbatim.
  - **"Names nothing" is the INDEX FIELD's zero, at every generation.** A
    malformed word carrying generation bits over a zero index is not equal
    to `NO_HANDLE` and must still resolve to nothing.
  - The generation SURVIVES an unbind and is incremented BY it, so a
    released word fails the equality test and becomes the ordinary
    `BadHandle` fault the callers already raise.
  - **BEST-EFFORT detection of a bug, not a uniqueness guarantee.** A word
    held across a full wrap of one slot's generation aliases silently;
    tables are per-process, so such a collision crosses no boundary and
    grants nothing. This is not to be "fixed" into unbounded bookkeeping.
- **Rights are a bitmask, SCOPED PER OBJECT KIND (ratified Aug 7,
  user).** Each kind defines its own backed rights enum —
  `SystemRight: UInt32 { case Transfer = 1, case Mint = 2, case
  Debug = 256, case Shutdown = 512 }`, `PipeRight { … Send,
  Receive }`
  — spelled `SystemRight.Debug`, scoped by the ENUM (no flat
  name-mangling; exhaustive match + the design-145 wire discipline
  come free). Kind-specific bits OVERLAP freely across kinds: a
  rights word is only ever interpreted against its handle's kind,
  which dispatch establishes before the check (§3 order), so each
  kind owns 24 bits instead of sharing 32. With §3's typed handles,
  the check helper demands the MATCHING right type
  (`check(h: SystemHandle, r: SystemRight)`) — testing a pipe
  right against a System handle is a COMPILE error. The exception:
  **UNIVERSAL rights own the pinned LOW BYTE — bits 0-7, identical in
  every kind's enum (ratified Aug 7, user: reserve room, more shared
  operations are coming).** Assigned so far: bit 0 = `Transfer` (may
  be sent over a pipe — the movability capability), bit 1 =
  `Mint` (may mint a SIBLING handle onto the same object); bits 2-7
  RESERVED (candidates as they prove universal: wait/signal,
  introspect/info, revoke).
  **THE DOCTRINE (ruled Aug 29, user, at M3 unit 3): SPECIFIC RIGHTS
  FOR SPECIFIC OPERATIONS, PER OBJECT TYPE; UNIVERSAL BITS FOR
  UNIVERSAL OPS; THERE IS NO GENERIC AUTHORITY.** `Manage` held bit 1
  from M2 until that ruling and meant "derive children" — general
  enough to be borrowed by four unrelated ops (`ProcessSelf`,
  `ThreadSelf`, `Give`, and a reading of sibling-derivation) and
  therefore specific enough to gate none of them. It is REMOVED, not
  renamed: `SystemRight.ProcessSelf`, `ProcessRight.ThreadSelf` and
  `ProcessRight.Give` are the bits that replaced it, each named for
  its op, so a launcher stripping one strips exactly one authority
  instead of an unpredictable bundle. `Mint` took the vacated bit
  (numbers are not ABI). What the doctrine does NOT mean is one bit
  per op NUMBER: `TimerRight.Arm` gates `Arm` and `Disarm`, and
  `WaiterRight.Attach` gates `Add` and `Remove`, because each pair is
  ONE capability and splitting it would produce a right that withholds
  nothing. The unit of a right is an AUTHORITY. Generic
  kernel paths (pipe handle-transfer) check universal bits without
  knowing the kind; `sosabi` `static_assert`s each kind's enum
  against the pinned table so no kind can drift, and a kind-specific
  right below bit 8 is a spec violation the assert catches.
  **`Transfer` HAS ITS FIRST CONSUMER** (sawos design 4, M3 unit 3): the
  bit was declared in every kind's enum in M2 and read by NOTHING until
  `ProcessOp.Give`, which checks it on the handle BEING GIVEN — at the
  one altitude that has the entry and has not yet branched on its kind,
  the same place `RELEASE_OP` is intercepted. So the universal low byte
  is now load-bearing rather than reserved, and `rights_allow_transfer`
  is the one function that reads it. Which kinds' DEFAULT sets mint it
  is a per-kind ruling: v1 mints it in `memory_rights()`,
  `process_rights()` and `root_system_rights()`, so a Thread, Event,
  Waiter, Interrupt, Clock or Timer handle cannot be given yet. Root's
  System handle GAINED it at M3 unit 3 (the M2 "nobody to transfer to"
  reason expired when unit 2 made a second process), which is what lets
  a launcher hand a child a masked System sibling — and is why a child
  is no longer console-silent.
- **THE INVARIANT IS NO AMPLIFICATION — and it always was.** REWRITTEN
  HERE at M3 unit 7, replacing the Jul-29 rule rather than amending it
  (sawos design 3 D-4 carried the compact amendment through unit 2.75;
  this is the statement it deferred). The old text read: "**No DUPLICATE
  right — no handle duplication at all**: every handle is unique, the
  exact `NoCopy` correspondence. If a second handle to a resource is
  legitimately needed, the resource's CREATOR (who holds MANAGE) mints a
  fresh one; there is no in-process copy. Attenuation happens only at
  that creation, monotonically." Three of those clauses are no longer
  true, and the one that is was never the point:
  - **ASKING TWICE GIVES TWO HANDLES.** Every getter of an existing
    object MINTS A FRESH ONE (`clock_get`, `process_self`,
    `thread_self` — unit 2.75's mint-per-call): no find-or-create, no
    handle-table search, no cached self-handle fact.
  - **`MANAGE` DOES NOT EXIST**, so there is no "creator who holds
    MANAGE" to re-mint through. The Aug-29 doctrine above removed it;
    rights are named for the operations they gate.
  - **ATTENUATION IS AN OPERATION A HOLDER PERFORMS**, not a property of
    what the kernel happened to mint at a creation — `MINT_OP`, below.
  What survives is the property the rule existed to protect: **no
  sequence of asks yields authority the asker did not already have.** A
  handle is still a unique ENTRY, which is the `NoCopy` correspondence §4
  builds on — but uniqueness was the mechanism, and no amplification is
  the law.

  THREE FACTS HOLD IT, each a line of kernel rather than a discipline:
  - **A MINT CARRIES THE KIND'S DEFAULT SET, or a subset of the source.**
    A getter mints the kind's default rights and nothing else;
    `MINT_OP` mints the SOURCE's rights INTERSECTED with a caller-supplied
    keep mask. Neither spelling can exceed what already exists.
  - **THE AUTHORITY TO MINT IS ITSELF RIGHTS-GATED** —
    `SystemRight.ClockGet`, `SystemRight.ProcessSelf`,
    `SystemRight.ProcessCreate`, `ProcessRight.ThreadSelf`, and the
    universal `Mint` for `MINT_OP`. A process that cannot spend the bit
    cannot perform the mint.
  - **A GIVE MOVES RIGHTS VERBATIM** (sawos design 4 D-1, M3 unit 3) —
    the same invariant read from the transfer side. `ProcessOp.Give`
    copies the entry's kind, target and RIGHTS WORD into the receiver's
    table and consults no default set at all, so what the giver
    attenuated stays attenuated and no handle is ever wider on the far
    side of a transfer than it was on the near one. The giver's entry is
    UNBOUND by the same act — `unbind_handle`, generation bump included,
    the same function release and the teardown call — so exactly one
    process holds the capability at every instant and the giver's word is
    a diagnosed `BadHandle` afterwards rather than an alias.

  **THE CLOCK IS THE WORKED EXAMPLE, and it is why mint-per-call is not a
  hole in attenuation.** A Clock is kernel-eternal and owned by nobody
  (§2), so `SystemOp.ClockGet` is a getter onto a well-known object and
  every ask mints another handle to it. Read carelessly that sounds like
  a leak — if asking always works, what does attenuating a clock handle
  buy? It buys everything, because THE ASK IS GATED TOO. A process whose
  System handle carries `ClockGet` could always read the clock, and a
  second handle adds nothing to what it already had. A process whose
  System handle had `ClockGet` masked out at the mint CANNOT ASK AT ALL,
  so a narrowed Clock handle it was GIVEN is the only clock it will ever
  have and narrowing that handle means exactly what it says. Attenuating
  a handle you give away is meaningful precisely when the receiver lacks
  its own minting authority — which is the launcher's ordinary case,
  since the keep mask that builds a child's System handle is where a
  launcher decides. What the extra INSTANCES buy is therefore ownership
  rather than authority: each is independently held and independently
  released, which is what makes §4's `NoCopy` wrapper an owner rather
  than a name, and it is exactly what the same-handle model could not
  give (two values wrapping one word double-release).
- **`Mint` IS THE SECOND UNIVERSAL BIT, and `MINT_OP` IS THE SECOND
  UNIVERSAL OP** (M3 unit 3; the Aug-29 rulings). A handle carrying it
  may mint a SIBLING onto the same object, in the caller's own table,
  whose rights are the source's INTERSECTED with a KEEP MASK the caller
  supplies. It is the ATTENUATE op §3 lacked. Four properties, and the
  first two are why it is safe to hand out:
  - **NO-AMPLIFICATION IS STRUCTURAL.** The result is a subset of the
    source by construction, not by a check that could be forgotten, so
    a garbage mask cannot widen anything.
  - **`Mint` IS ITSELF MASKABLE.** A sibling minted through a mask that
    omits it cannot mint again, so proliferation stops where the mask
    says it does.
  - **THE MASK IS A KEEP SET, WHICH FAILS CLOSED.** A mask written
    before a kind gains a right is a whitelist that has never heard of
    it and therefore denies it. The removal polarity fails open, and the
    case that decided it is a launcher masking System handles for
    children — where an old mask would have handed every future System
    capability to every child ever configured.
  - **IT IS TOTAL, and needs no introspection.** There is no
    rights-introspection op; naming a right the source lacks is a no-op,
    so nothing can be over-asked and nothing has to be read first.
  It also CLOSES design 3's finding 2 ("no op mints a second handle onto
  an object a process already owns"), which was the blocking limitation
  of the launch flow: a launcher now mints what it hands over and keeps
  its own, so supervision and self-management stop being alternatives.
- **Attenuation is monotonic**: any derivation may only strip rights,
  never add. The only rights source is still the boot handle set given to
  the root server, so every rights word in the machine is a subset of one
  of those, reached by some path of mints and gives. `MINT_OP` made it two
  things it was not before. It is STRUCTURAL rather than a rule to obey —
  a sibling's rights are an intersection, so no sequence of mints recovers
  a masked bit — and it is PERFORMABLE, so a launcher narrows a capability
  itself instead of depending on what the kernel happened to mint.
  **AND MONOTONICITY IS WHAT MAKES A RIGHT A POLICY KNOB. The worked
  example is M3 unit 6's executable gate** (sawos design 9 D-1, §2.5).
  `MemoryRight.MapExecute` gates `MapAccess.Execute` on a `map`, so "only
  root maps executable" is a fact about capability FLOW rather than about
  identity: `memory_rights()` MINTS the bit at boot — of necessity,
  because a bit not minted at boot could never appear later — and root
  simply never grants it, narrowing a child's region handle with a keep
  mask before the region travels. The kernel asks no question about who
  is calling. The policy lives entirely in a mask a launcher writes,
  which is what a right named for its op buys and what a generic `Manage`
  could not have expressed at all.
- Handle close is explicit in ABI, automatic in Saw (Deinit).
  **RELEASE IS BUILT; CLOSE IS NOT** (sawos design 3 D-2, M3 unit 2.75),
  and the two are different acts that were once one word. RELEASE
  destroys the CALLER'S HANDLE: one universal op number
  (`sosabi.RELEASE_OP`, documented forever outside every per-object
  table, which is dense from 0), intercepted by dispatch between the
  table lookup and the kind match; UNGATED, since destroying your own
  capability instance harms nobody and identity lives in the object's
  slot; it unbinds the entry, bumps the generation and never touches the
  object's STATE. **IT DOES, SINCE M3 UNIT 5, TOUCH THE OBJECT'S
  REFERENCE COUNT — AND ZERO FREES** (sawos design 7 D-1, amending unit
  2.75's "an object whose last handle is gone is unreachable-but-live
  until its process's teardown"; that era ends here). Every countable
  slab slot carries a count of the handle entries naming it, plus, for a
  waitable, its attachment; a release decrements, and a count reaching
  zero frees SYNCHRONOUSLY, inside the very syscall that dropped the
  last reference — the kernel runs to completion, so there is no
  deferred reclamation and no cleanup queue. The kind's own last rites
  run there: an Interrupt MASKS ITS LINE and becomes re-bindable, a
  Timer gives the comparator back, a Waiter DETACHES ITS LIST (which may
  take a waitable to zero in turn — the one cascade), a Memory returns
  its SLOT and no bytes (§2.5's narrowing), and a `Gone` process's slot
  is reclaimed, which is design 3 D-3's scan turned into the count it
  was approximating. **THE ONE EXCEPTION IS A MAPPING**, whose row is not
  a reference back: it frees its slot and LEAVES the row installed, which
  is §2.5's own permanent-but-safe stance made mechanical. Releasing a
  word that does not resolve — `NO_HANDLE`, a malformed word, a stale
  one, a second release — is the ordinary
  `BadHandle` fault. In Saw it is automatic: every `sos` wrapper is
  `NoCopy` with a `deinit` that releases, so DROP IS RELEASE and there is
  no typed `release()` method to write. CLOSE — ending the OBJECT for
  everyone — is still unbuilt: it is object-protocol, exists only on kinds
  with an end-state, and arrives with Pipe in M4 under a per-kind right.
  A process's remaining handles are still released by the ratified
  teardown.
- Syscall ABI sketch (riscv32 `ecall`, args in registers): every call
  is `(handle, op, args...) -> Result`. The kernel's dispatch is a
  table lookup + rights check + object-op — the fast path must stay
  tens of instructions.
- **Typed handles in the Saw API (ratified Aug 7, user).** Each object
  kind gets a DISTINCT handle type in the `sos` module —
  `type SystemHandle = UInt`, `type PipeHandle = UInt`, … — and the
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
  TABLE indexes by word. This is TIER ONE (kind safety) of two, and
  **TIER TWO IS BUILT** (sawos design 3 D-5, M3 unit 2.75; the Aug-29
  drop-is-release ruling). Every wrapper is a `NoCopy` struct over the
  aliases with a hand-written `deinit` that releases the word — the
  TcpStream pattern, and §3's no-DUPLICATE rule is its exact `NoCopy`
  correspondence — so the alias became the payload and nothing written
  against it changed, exactly as the additive claim promised. M2 brought
  neither half; 2.75 brought both TOGETHER, because a release op beside
  copyable wrappers is a stale-fault factory (copy, drop, and the
  sibling's next use is a manufactured `BadHandle`). Three consequences:
  - The word field doubles as the DISARM SENTINEL, since `NO_HANDLE` is
    unrepresentable as a live handle at any generation.
  - There is deliberately no typed `release()` method. Early release is
    dropping the value (`let _ = move w`), which is one concept rather
    than two spellings of it.
  - **THE TRANSFER-FUNNEL CONTRACT**, recorded for unit 3's `give` and
    M4's pipes: when an op MOVES the word out of the caller's table, the
    sysapi funnel consumes the wrapper, reads the word, sets the field to
    `NO_HANDLE` BEFORE the syscall, and lets the disarmed value drop.
    Disarm-before-syscall because every failure of such a syscall is a
    fault (the process ends, teardown covers everything), so no path
    leaves a disarmed-but-unsent word. User code never touches the
    sentinel; generations backstop the discipline, so a funnel bug that
    released after a transfer is a diagnosed `BadHandle`, not corruption.
    **IT HAS ITS FIRST CONSUMERS** (sawos design 4, M3 unit 3):
    `Process.give(memory:tag:)` and `give(system:tag:)` take their wrapper
    BY VALUE and disarm it before the syscall, so a given capability is
    unreachable from the giver by construction rather than by discipline.
    `Process.start(donating:)` is the one place the wrapper is DISARMED
    rather than consumed — Saw has no consuming `self` receiver, and the
    handle being moved is the one the op is invoked through — so it leaves
    a husk whose drop does nothing and whose use is the ordinary
    `BadHandle`.

## 4. The Saw synergy (why this language, this kernel)

- **Handle lifecycle = ownership.** Userspace handles are
  `NoCopy + Deinit` wrappers: scope exit closes (capability leaks are
  a compile error class); `move` is transfer;
  `pipe.send(move h)` enforces at COMPILE TIME that the sender no
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

1. ~~Pipe semantics~~ RESOLVED §2.1 + **base-send DECIDED (user,
   Jul 31): STRICT RENDEZVOUS.** Every send blocks until a receiver
   takes the message — the kernel holds NO message buffers (bounded
   only by waiting senders; zero-copy handoff at the rendezvous).
   Async notification is the Event object's job (§2.4). Request/reply:
   `call` = rendezvous handoff + block on the PipeReplyHandle (AMENDED
   Aug 20: no separate `call` verb — the send-and-wait form is the
   suspending overload of `send`; §2.1). Message
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
   (PMP/APM per §5.5) → mint root handles (boot pipe, root
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
   STATUS, not an error (`SosStatus.Ok`, `SosStatus.NoResource`, …), and
   the `Sos` prefix names whose status it is. The hosted runtime's
   `SysError` in rt/ABI.md is a SEPARATE frozen contract (errno tags,
   machine-parsed since design 149) and keeps its name; the spec notes
   the correspondence, nothing more. An `Err(SosStatus.Ok)` never
   arises by construction: the wrapper boundary splits on the status —
   `Ok` → `Result.Ok(value)`, anything else → `Err(status)`).
   **THE STATUS SPACE NARROWED AT M2** (the fault-don't-status ruling,
   design 178 pin 6, ratified Aug 8): a caller error the process could
   have checked — an invalid handle, a malformed op, a rights violation —
   TERMINATES the offending process, so `BadHandle`, `BadOp` and
   `AccessDenied` are `FaultReason` tags rather than statuses, keeping
   their numbers. What is left in `SosStatus` is what a caller could not
   have known — `NoResource`, a full slab — with `Unknown` still the
   userspace mapping artifact. M3 adds `QuotaExceeded` beside it (design
   232 unit 5) on the same line design 178's fourth round drew: a
   dynamic resource condition answers with a status, broken code faults.
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

   **AMENDED FOR M3** (M3 unit 7's docs sweep, discharging the spec edit
   design 178's fourth round owed and deferred to this milestone). TEN
   object kinds joined the surface across M2 and M3 — one at M1b, eleven
   now — and the shape above did not move for any of them; three things
   the ladder settled about it are worth stating here, where the ABI is:
   - **RIGHTS-GATED OPS ARE THE WHOLE OF THE SURFACE, and every gate is
     named for its op.** There is no bare numbered syscall, no ambient
     facility, and — since the Aug-29 doctrine (§3) — no generic
     authority anywhere in the ABI: an op is reached through a handle of
     the right kind and spends a bit named for that op. §12's "become
     real objects in M3" line is what this amends. The pool roots DID
     become objects (`Memory`/`IoMemory`, M3 unit 4, delivered through
     the boot set), and the root IRQ TABLE DID NOT — binding a line is
     `ProcessRight.InterruptBind` on a handle a process already holds,
     which is §3's derivation rule rather than a second mechanism beside
     it. A right where the Jul-29 draft imagined an object is the
     cheaper answer whenever the "object" would have been a table with
     no state and one verb.
   - **THE BOOT SET IS ONE HANDLE WIDE AT THE ENTRY REGISTER, and every
     kind added to it has kept it that way.** A process enters user mode
     with exactly one word — its System handle — and everything else the
     kernel or a launcher minted for it DRAINS through
     `ProcessOp.BootHandleNext`, one `{tag, kind, handle}` record per
     call (§12). Regions, device windows and masked System siblings have
     all joined that set since M1 and the register count has not changed,
     which is the property the design existed for: the entry ABI is
     fixed and the furnishing is open-ended.
   - **TWO COPY FUNNELS ARE THE ONLY PLACES THE KERNEL TOUCHES USER
     MEMORY.** `copy_out` (§2.2 — the wait record and every answer wider
     than the registers, including the boot-set record above) and its
     mirror `copy_in` (design 232 unit 1 — `Timer.Arm`, and M4's message
     body) are the only code in the kernel that dereferences a user
     address. Each validates alignment and containment and TERMINATES
     the process on a violation, on this item's own faults ruling; the
     asymmetry between their windows is deliberate and is stated at
     §2.2. That is what keeps "where does the kernel read user memory"
     a two-site audit rather than a property of the whole dispatch
     table — and it is what makes a per-address-space mapping switch a
     one-place change if Profile B ever grows real translation.

   **AND M4 UNIT 3 GREW AN OP'S ARGUMENT LIST WITHOUT COSTING A REGISTER**
   (sawos design 15; `designs/010` rulings 9(b) and 11(c)). `Waiter.Add` took
   two arguments and now takes three: a target, a key, and a FLAGS word — bit 0
   detaches the attachment at delivery, bit 1 releases the caller's handle entry
   into the attachment's keeping. Two things about that are this item's own
   claims paying out:
   - **THE REGISTER WAS ALREADY THERE.** An op has `a1`-`a5` and the raw form is
     fixed-arity, so `sos_waiter_add` was riding the three-argument raw form with
     a zero in the third slot. Growing the op edited one seam and one dispatch
     arm and nothing about the convention. The renumberable-op discipline is
     what made unit 1's `Post` grow a return and unit 2's `Take` grow a record;
     this is the same freedom on the ARGUMENT side.
   - **A FLAGS WORD OUTSIDE THE PUBLISHED SET IS A FAULT, not a status**, on
     this item's own fault-don't-status line: the two bits are published in the
     module userspace links against, so a word carrying any other is a mistake
     the caller could have checked. The typed surface cannot spell one at all —
     `AttachMode` has two cases and the consuming half is a distinct VERB
     (`Waiter.give`) — so the four legal combinations are the only things a Saw
     caller can ask for, and the kernel's check exists for the C altitude
     beneath it.

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
object/handle/pipe/syscall model is IDENTICAL across profiles —
divergence is confined to a small per-arch HAL (boot, trap entry,
context switch, Mapping/AddressSpace implementation), selected at
build time via the module-path mechanism (`--module-path
hal=hal/<target>/kernel` — Blade/B0 machinery). All wire/boot formats
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

- **The kernel has no architecture in it.** `kernel/core/` names no
  register, no trap cause, no protection hardware and no board; it reaches
  all of it through one module it imports as `hal`, mapped per build to
  `hal/<arch>/kernel/`. The harness SCANS that directory for
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
- **The same root server sources build for both.** `root/src/` is
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
arena and the four `__saw_rt_*` seams are Saw in `rt/common/src/lib.saw`,
one copy serving the kernel and every process; the process side's two hooks and
its parked boot handle are Saw in `kernel/sysapi/`, beside the System object
whose authority they spend. Both user HALs are now their syscall instruction and
nothing else, and `rt/common_c/support.c` is `mem*`, the atomic libcalls and
(since design 232 unit 1) the 64-bit division pair — reason 2, and reason 2
only. That last entry is worth its line: a Clock reads NANOSECONDS, which is a
64-bit quantity on every profile because 32 bits of them wraps in four seconds,
and rv32 has no 64-bit divide instruction even with `+m`. So the floor grows
when a 32-bit machine meets an arithmetic width the ISA does not have — not
when the language falls short.

So the floor is one shared C file plus four inline-asm leaves, and every one of
them is reason 1 or reason 2. Reason 3 is the only open language gap.

**M2 moved both numbers and added no reason** (design 178's per-unit
measurements): C went 135 -> 168 code lines — one interrupt-class mask register
on Profile A and four timer system registers on Profile B (unit 1),
`sos_syscall3` per profile (unit 2: an op that answers with a VALUE needs one,
and the C ABI the Saw side declares against has no aggregate return), and
`sos_wait_for_irq` per profile (unit 4: `wfi` is an instruction). Assembly went
UP by 13 for Profile B's interrupt vector entry and then DOWN to 268 total,
because a thread context built in Saw needs no register-clearing prologue —
`enter_user` is gone from both HALs, replaced by a Saw `frame_init` and a
`resume_frame` that branches into the trap entry's own restore path. Reason 1 in
every case: an object model grew by five kinds and the diet's direction did not
change.

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

**STATUS AFTER M2: none of this section is built** (design 178 D3, ratified
Aug 15). The kernel schedules ONE round-robin FIFO — no levels, no ready
bitmap — on a one-tick slice, and no shipping kernel image even arms the
tick: `kernel/main.saw` arms none, and the harness kernels that test
preemption arm their own. The §7 map stays a LOADER ARTIFACT: the loader parses the
image's priority field and REPORTS it, no Process slot stores it, no syscall
carries a band tag, and there is therefore no enforcement point yet. Two
consequences worth reading here rather than deducing: `create_process` and
its LAUNCH capability do not exist (M3, design 232 unit 2), and "nothing
runnable" is not simply the idle case — it is idle when some interrupt line
is bound and the ratified deadlock report otherwise (§9). Round-robin is
ruled to stay through M3 (design 232 agenda item 10); what follows is what
SMP-era work builds.

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
  launcher service over a pipe.
- **The map is immutable after creation.** No remap syscall, no
  self-modification, no visibility into the map from inside the
  process (each process sees only its 4 named bands). Changing a
  running process's priority = restart it. (A future dynamic
  re-prioritization design could add a LAUNCH-gated syscall without
  disturbing anything here.)
- **No priority inheritance.** Inheritance chains through transferable
  PipeReplyHandles are ill-defined (the reply obligation migrates). The
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

**BUILT M3 unit 2 — THE SECOND ADDRESS SPACE** (sawos design 2). Process
creation is real, and the lifecycle it is built on is the one ruled Aug 16:
**TWO PHASES, INERT UNTIL STARTED.** `SystemOp.ProcessCreate` (on the caller's
SYSTEM handle, gated by `SystemRight.ProcessCreate` — the Aug-29 receiver ruling
amended into §12: processes are minted by the System) takes two
Memory regions — a read-only image blob and the child's destination RAM — and
returns a process with an address space, a recorded entry and stack, and NO
THREAD. `ProcessOp.Start` (on the CHILD's handle, gated by
`ProcessRight.Start`) mints the first thread at that recorded entry and makes
it runnable; the caller keeps running. The ordering IS the soundness argument
for the launch flow: whatever a launcher gives a child must be complete at its
first instruction, and the start call is the barrier that makes a
half-populated table unrepresentable — with no synchronization invented. **A
SECOND `start` is a `BadState` fault** (ruled): broken code, and the caller is
the one that started it the first time. **A malformed image is a `BadImage`
STATUS, not a fault** — the one caller-supplied thing that does not end the
caller, because image bytes are DATA a launcher was handed rather than
something it wrote (the `from(raw:)` precedent).
**THE FAULT RULE BELOW NOW FORKS.** A thread fault still kills its process and
the teardown still runs unconditionally; what changed is what happens after.
Process 0 — root, the one image the kernel loads (§12) — stops the machine,
byte-identically to every earlier era. **ANY OTHER PROCESS RESCHEDULES**: its
threads are REMOVED from the ready queue (leaving everybody else's), its slabs
and its protection rows go back, and the path ends in the scheduler rather than
in a stopped machine. Children die with the machine when root ends, uncounted;
observing a child's death as an event is unit 5.5's death notifications, BUILT
since (the Process-waitable bullet below); either way a supervisor may read
`get_status` through the handle it still holds —
which outlives the child, because the teardown closes the handles in the DEAD
process's table and not in its creator's.
**`process.kill()` still has neither op nor right.** The second process exists
now, so the argument that made it degenerate has expired; what it waits on is a
ruling about what killing a process that is not the caller does to a scheduler
that may be running its thread.

**STATUS AFTER M2** (design 178 D4, ratified Aug 15). BUILT: the fault rule
below, whole — a thread fault kills its process and the ratified teardown
runs unconditionally — and `get_status` as `ProcessOp.GetStatus` gated on
`ProcessRight.Wait`, answering the one fixed-width word (kind in the high
bits, `Running` beside the three ratified ones, code in the low). **`Join`
EXISTS, which supersedes the "no join syscall" line below**: D4 gave Thread
`Start`/`Join`/`Exit`/`Yield`, and a joiner parks on the target's list and is
answered exactly once through the funnel §2.2 describes. The observability
this section reserved for stack reclamation therefore arrived a milestone
early, because the wait/wake substrate needed a first consumer. **AND THE
PROCESS HALF OF THE WAITABILITY LINE BELOW IS BUILT SINCE M3 UNIT 5.5** (sawos
design 8), amending this paragraph's own "NOT BUILT" of the M2 era: a Process
handle attaches, on `ProcessRight.Wait`, and its death is the terminal level.
STILL NOT BUILT: a THREAD handle is not waitable — attaching one is a
`NotWaitable` fault, and since M4 unit 3 closed §2.2's list over the four pipe
arms it is the LAST kind in the tree for which that arm is a scope line rather
than a statement about the object. (`process.kill()` was named here too, on the M2-era reason
that with one process it would be `Exit` under a second name. Unit 2 loaded the
second process, so that reason expired; the deferral is stated ONCE, with its
current reason, in the paragraph above.)

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
  primitive userspace supervision parks a Waiter on. **BUILT M3 unit 5.5
  (sawos design 8), and it is the existing Process object becoming
  waitable rather than a new kind.** §2.2's `waiter.add(handle, key)`
  takes a Process handle
  on `ProcessRight.Wait` — the SAME bit `get_status` below spends,
  because being woken by a death and asking about one are the same
  question at two tempos — readiness is the slot being `Gone` whatever
  ended it, and the payload the record carries is exactly the status word
  the next bullet defines (`WaitTag.Process`,
  `WaitPayload.Process(status:)`). It adds no vocabulary to §8; it adds a
  second DOOR onto §8's word. **DEATH IS A TERMINAL LEVEL**: the delivery
  consumes nothing, so a waiter attaching after the death still wakes and
  a second wait answers the same word — which is what makes supervision
  race-free, since there is no window in which a death can be missed. The
  attachment counts as a reference on the process slot (§2's counted-kinds
  table), so a watched dead process's slot survives until the watcher
  detaches. THE ORDERING: the kernel notifies once the status word is
  recorded and BEFORE the teardown runs, but a notification only queues a
  thread, so the woken supervisor runs after the teardown has completed
  and no observer ever sees a half-dead process.
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
    **RETIRED IN TWO STEPS, AND BOTH ARE DONE.** M3 unit 4 made the grant
    an `IoMemory` a driver OBTAINS from its boot set and maps into itself
    (§2.5's migration case); M3 unit 6 made it a capability a LAUNCHER
    HANDS OVER — see the driver-child bullet below, which is where a
    driver's authority now comes from.
- **BUILT M3 unit 6 (sawos design 9), and it is the section's finale: A
  DRIVER IS A CHILD PROCESS.** Every earlier §9 sentence is unchanged —
  mask-on-fire, ack-to-rearm, ack-is-a-release, one task one Interrupt —
  and what changed is WHO the driver is:
  - **A DRIVER'S DEVICE ARRIVES FROM ANOTHER PROCESS.** Root drains the
    console's register page as an `IoMemory`, `give`s it to a child under
    a tag (spending the universal `Transfer` bit on the window and
    `ProcessRight.Give` on the child), and the CHILD maps it into ITSELF
    with `Process.map(iomemory:)`. The lean is deliberate: what crosses
    the boundary is authority over a device, and where the row goes is
    then the holder's own business. Root never maps the window, never
    binds the line, and never learns the UART's address.
  - **THE DRIVER PROGRAM DID NOT CHANGE.** The two child packages are the
    two root-as-driver packages, driver body for driver body — same
    registers, same enable bit, same drain-before-ack loop, same echoed
    bytes, same line number — differing only in that the window arrives
    by give, that they end with `Process.exit` (a driver child holds no
    `SystemRight.Shutdown` and should not), and that they are linked at
    the child base. Both twins stay in the suite, so the claim is two
    transcripts side by side rather than an assertion.
  - **THE TWO RIGHTS A DRIVER SPENDS ON ITSELF ARE NOT ITS LAUNCHER'S TO
    WITHHOLD**, which is a recorded finding rather than a design.
    `ProcessRight.InterruptBind` and `ProcessRight.Map` arrive on the
    handle `SystemOp.ProcessSelf` mints, and that op mints the ONE Process
    default set; a launcher's keep mask reaches the child's SYSTEM handle,
    not the Process handle the child derives from it. Withholding them
    would need `ProcessSelf` to take a keep mask of its own. Nothing in
    v1 needs that — a driver child is exactly the process that should hold
    both — and it is named here so the gap is visible.
  - **THE SUPERVISOR PARKS ON THE DEATH, NOT ON A DEADLINE.** A driver
    waits on a real serial port, so a timer would have to be guessed at —
    and §9a's one bounded exception to the console handover is a kernel
    whose armed tick narrates over a process that owns the device. Unit
    5.5's Process waitable is what makes a launcher able to arm nothing.
- **BUILT M3 unit 1.5 (sawos design 1), and what it amends here.**
  Delivery gains a THIRD entry point beside the trap path and the idle
  poll: a PREEMPTION POINT inside a long kernel operation. The sentence
  design 178's D2 is now written in:
  - **The hardware never delivers an interrupt in kernel mode; the kernel
    ASKS, at named points.** The first half is unchanged and still
    enforced by both machines — an interrupt arriving in kernel mode is
    each HAL's kernel-bug path, which stays a live tripwire rather than
    becoming a delivery path. What is new is that a long operation POLLS,
    through the same `irq_poll` the idle path already needed and the same
    delivery funnel. No new seam, no brief unmask, no nested trap frame.
  - **A point interrupts LATENCY, NOT ATOMICITY.** A reschedule the
    delivery signals is honored where it always was, at the user-return
    boundary; the operation still completes before the switch it may have
    motivated.
  - **The placement rule is one reviewable sentence per site**: a point
    is legal where no in-flight invariant spans state the IRQ path
    touches. The design carries the audit of every kernel-mode loop, and
    the verdicts live as comments at the sites.
  - **One guard backs the rule mechanically** — a kernel flag set around
    the delivery funnel's body and checked first at every point, so a
    point reached from INSIDE delivery is inert. It exists because the
    byte movers are shared: the loader's segment placement is preemptible
    boot context and §2.2's record copy-out runs in IRQ context.
  - **What still takes no point**, recorded rather than hidden: the
    console write loops and the UART ready spin (device-drain-bounded, a
    long diagnostic line IS masked latency), the controller and grant
    walks (half-configured hardware is IRQ-shared state in flight), the
    bounded slab scans, and process teardown (the scheduler is dismantled
    across it).

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
  `hal_irq_restore(Flags)`, under `hal/<arch>/kernel/`. The CAS
  stays even on uniprocessor (SMP-future correctness; under `+a` it is
  cheap; a FAILED acquire on uniprocessor signals reentrancy — panic,
  never spin forever).
- Timing: DESIGN ratified Aug 6. **STILL UNBUILT after M3 unit 1.5, and
  correctly so.** The M2-era interrupt work landed under design 178's
  D2 — interrupts are taken from user mode only, enforced by both
  machines rather than intended by the kernel, and the idle path POLLS
  rather than taking the trap (§9) — so the kernel held no state an ISR
  could interrupt and there was no critical section for the type to
  protect. **Kernel interruptibility has since been BUILT (sawos design
  1) and did not change that**, which is the amendment worth recording:
  it landed as explicit PREEMPTION POINTS, and a point IS the assertion
  that state is consistent, so it needs no lock — what it needs is a
  placement sentence per site plus one reentrancy guard for the code the
  two contexts share. SMP — sequenced after pipes — is what still forces
  the lock, and the point placements are now a REAL map of where it must
  go rather than a promised one.

## 10. Userspace runtime: HandlerGroup + the wake bridge (ratified Aug 3)

Saw's sequential surface is UNCHANGED: `pipe.send()`, `receive()`,
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
  Pipe → `(&Pipe, msg, PipeRequestHandle)` — the PipeRequestHandle is
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
- **Orchestrator pins SETTLED by M1/M1b/M2, and still settled after M3:** the
  rights-word bit assignments and the per-object op tables are concrete and
  kernel-internal (`kernel/abi/`, one file shared by the dispatch and
  the wrappers — §5.7's vDSO discipline is what keeps them renumberable, and
  M3 renumbered inside it twice without a userspace edit: `Manage` vacated bit
  1 for `Mint`, and five kinds joined `ObjType`);
  the per-profile kernel memory layout is two linker scripts under
  `hal/<arch>/kernel/` over per-type static slabs; the `sosimg`
  header is concrete at v3, the §7 priority-map field included, beside the
  build-emitted REGION TABLE at v2 whose kind column M3 unit 4 added.
- **Orchestrator pins STILL OPEN, restated at M3's close:** where the
  PHYSICAL-REGION refcount lives (§5.9). M3 unit 4 made something ALLOCATE
  physical regions (`Memory.Split`) without answering it, and M3 unit 5 built
  a refcount that answers a DIFFERENT question — it counts the handle entries
  naming a SLAB SLOT, and reaching zero returns the slot. Bytes are still
  unaccounted: a freed Memory returns no range to any pool, because quotas
  count OBJECTS in v1 (§2.5's narrowing), so the pin now has a caller, a
  neighbouring mechanism, and still no home. It lands with a real allocator
  (M4+). Also open: §2.1's message limits (pinned here at a 64-byte body /
  4 handles, and SUPERSEDED by designs/010's ruling 6 — 128 bytes, 4 handles,
  an in-flight budget of 2 × `MAX_THREADS`, all build defines — none of it
  built until M4 lands the Pipe); and the §7 priority map plus root's
  bootstrap band map, which the loader parses and REPORTS while no Process
  slot stores either.
- **What the kernel does NOT have after M3.** One area per line, with the
  section that specifies it. The M3 rows below are struck as the ladder
  landed them; design 232 was the M3 plan of record and `designs/010` is
  M4's, and each is pointed at rather than duplicated.
  - ~~**Clock and Timer**~~ BUILT by design 232 unit 1 — see the M3 entry in
    the roadmap below. A process can sleep.
  - ~~**A second process**~~ BUILT by sawos design 2 (M3 unit 2).
    `process_create(image:memory:)` + `start()` are real, §12's
    loader-above-boot rule is EXERCISED — one loader, two doors, differing
    only in region parameters and failure vocabulary — and the protection
    domain is RELOADED per process at the scheduler's one switch point.
  - ~~**The launch flow — a child that RECEIVES something**~~ BUILT by sawos
    design 4 (M3 unit 3), and it is what turns a second address space into a
    second PROCESS. `give(handle, tag:)` moves a capability into a child's
    table under a word the launcher chooses and the kernel never reads;
    `start(boot_tag:)` resolves that word to the child-side handle and puts
    it in the child's first argument register; `BootHandleNext` drains the
    caller's own PER-PROCESS set, so a child's boot sequence is literally a
    receive loop. THE BOOT SET FREEZES AT START — a give afterwards is
    `BadState` — and that ordering is the whole soundness argument: what a
    launcher furnishes must be complete at the child's first instruction, and
    the barrier makes a half-populated table unrepresentable with no
    synchronization invented. A child handed nothing is STILL the ruled
    "legal-but-doomed" sandboxed compute process, which is a feature and the
    default.
  - ~~**Attenuation as an OPERATION, and a second handle onto one object**~~
    BUILT by the same unit, and the two are one op: `MINT_OP` (§3) mints a
    SIBLING onto the object a handle names, in the caller's own table, with
    rights the source's INTERSECTED with a KEEP MASK. It closes design 3's
    finding 2, it makes §3's monotonic attenuation something a HOLDER performs
    rather than a property of what the kernel minted, and it is what lets a
    launcher hand a child a MASKED SYSTEM HANDLE — so a child bootstraps
    exactly as root does (§12's symmetry), prints without owning a device, and
    cannot stop the machine. `Mint` is itself maskable, so proliferation is
    revocable. What the flow still lacks, named rather than pending: a
    handle cannot be narrowed BELOW `Transfer` and still be given (the give
    checks that bit on the thing given, so a receiver holds it too — inert
    today, a ruling for the unit that gives a child a pipe); and dynamic
    transfer to a RUNNING process is M4 IPC's, over pipes, to a receiver
    expecting it.
  - ~~**Memory / IoMemory / Mapping and `map()`**~~ (§2.5) **BUILT by sawos
    design 6 (M3 unit 4)**, over unit 2's first slice. **A MAPPING IS AN
    INSTALLED GRANT ROW**, which is what the unit costs so little: SOS does
    not translate (§5.5), so the grant record M3 unit 2 built for the
    SCHEDULER is the record `map` edits and the object is a handle onto one
    row of it. Three kinds because the operations differ — `Memory`
    (`Split`/`Map`), `IoMemory` (`Carve`/`Map`, a distinct KIND so a device
    region cannot produce a cacheable mapping by construction), `Mapping`
    (`Unmap`, and only it unmaps). Derivation is one cut FROM THE FRONT for
    RAM (the parent is the pool cursor; arbitrary-offset carving is refused by
    the SHAPE) and non-exclusive for device windows (§2.5's "handed out many
    times"). Access is PER MAPPING, so double-mapping one region RO here and
    RW there is expressible and allowed. **THE LIVE-DOMAIN RULE** is the one
    piece of law the unit added: a grant-record edit reloads IMMEDIATELY when
    that domain is the installed one, because `run_thread` skips equal domains
    and a revocation that waited for a reschedule would not revoke. The M2
    device-grant placeholder is RETIRED IN-TREE — both driver packages obtain
    their window instead of declaring it, same bytes, same line number — with
    the sawlang-side `SegFlag.Device` deletion left as a pin-bump backlog
    entry. What unit 4 left absent was FREE-ON-LAST-REFERENCE — a split was
    permanent until teardown and teardown returned SLOTS, not ranges — and
    **M3 UNIT 5 BUILT IT AND NARROWED IT**: the slot comes back inside the
    release that drops the last reference, and the RANGE still comes back to
    nobody, because quotas count objects rather than bytes in v1 (§2.5 states
    the narrowing in the refcount clause's own words).
    **AND M3 UNIT 6 (sawos design 9) FINISHED IT, in the two directions §2.5
    named and one the ladder ruled on the way past.** SHARED MEMORY is real —
    one region mapped into two processes, written from each side and read from
    the other — and the launcher installs the second row itself, so a process
    can be given ACCESS without possession of the region object. A DEVICE
    WINDOW travels the other way: `give(iomemory:)` moves the console's page to
    a driver child, which maps it into itself and echoes the harness's bytes on
    both profiles. And EXECUTABLE BECAME AN AUTHORITY —
    `MemoryRight.MapExecute` gates `MapAccess.Execute` per handle, and
    `Write | Execute` in one row is a `BadArg` — which is the one piece of law
    this unit added and is written out at §2.5. What stays absent is unchanged:
    a freed Memory returns its SLOT and not its BYTES, and a region reaching a
    RUNNING process is M4 IPC's.
  - **Quotas** (§12's creation-authority pin, which M2 ANSWERED with a
    factory-capability rights bit rather than a quota) — the per-process
    table, `QuotaExceeded`, and creator-pays accounting are design 232
    unit 5. A full slab answers `SosStatus.NoResource` today.
    **BUILT M3 UNIT 5 (sawos design 7 D-3).** The table is
    `kcore.objects`' two flat per-process rows (used and limit, one column
    per `QuotaKind`), charged at every allocation site that answers
    `NoResource` — POLICY FIRST, so a process at its budget is told
    `SosStatus.QuotaExceeded = 7` and only one still within it can meet
    the machine's edge. Accounting is CREATOR-PAYS, against the slot's own
    `process` field, with ONE ruled exception: a Mapping charges the
    TARGET, because what it consumes is that process's grant-row budget
    and charging the caller would let a launcher's allowance gate another
    process's domain size. ROOT'S ROWS ARE UNLIMITED — root is init and
    its policy cap IS the machine, so the slab is what refuses it — except
    its mapping row, which is clamped to its own free grant rows; that
    clamp is what makes agenda item 8's ruled `fatal` (a `map()` meeting
    the physical wall with quota headroom left) unreachable rather than
    merely unlikely. A child gets `kcore.limits`' documented defaults;
    there is no per-create quota argument in v1.
    **M4 UNIT 1 ADDED THE TENTH COLUMN, `QuotaKind.Pipe`** (sawos design 13),
    and it is the first row that counts a CONNECTION rather than an object with
    one name: `ProcessOp.PipeCreate` charges it once and the credit lands when
    BOTH of the connection's reference columns reach zero and the slab slot goes
    back. Charging per ENDPOINT was considered and refused — two ends of one
    connection are two handle entries, which the `Handle` row already counts, so
    a second charge would make a launcher that keeps both ends pay twice for one
    slot and would make giving an end away look like a refund. Creator-pays
    means a child GIVEN an end spends nothing here, which is what lets a server
    child hold many sessions under a small pipe budget.
    **M4 UNIT 2 ADDED NO COLUMN AT ALL, and that is a ruling rather than an
    omission** (sawos design 14 D-1). A one-shot pair — a `PipeReply` claim and
    its `PipeRequest` obligation — is RING-SLOT STATE inside a connection the
    creator was already charged for, so the scarce thing an exchange occupies is
    already paid for: `QuotaKind.Pipe` covers the row and its whole staging ring,
    and a per-exchange row would charge a client twice for the same kilobytes.
    What an exchange DOES spend is `QuotaKind.Handle`, twice — one entry for the
    claim at the post and one for the obligation at the take — which is the
    ordinary rule that a capability costs a table row, and which is also what
    design 10 ruling 4 means by "zero one-shot quota rows on the fast path": when
    `Call` fuses the composition, the claim is kernel-internal and even those two
    entries stop being minted, a difference of HANDLE TRAFFIC and of nothing
    else.
    **M4 UNIT 3 ADDED NO COLUMN EITHER, and it is the same argument one level
    out** (sawos design 15). An ATTACHMENT is a slab slot too, and it is bounded
    by `MAX_ATTACHMENTS` rather than by a quota — which unit 3 did not change,
    because an attachment already costs the ONE thing quotas count on this
    axis: a Waiter to hold it, charged at `QuotaKind.Waiter`. What the unit's
    CONSUMING attach does change is the handle side, and in the caller's favour:
    a client that gives its claim to a subscription spends no `QuotaKind.Handle`
    row at all while the request is outstanding, which is design 14 finding 5's
    answer — `MAX_HANDLES` had been the real bound on a client's in-flight
    depth, and it stops being one.
    **AND THE TEARDOWN NOW WRITES OFF RATHER THAN FORCE-FREEING** (`designs/010`
    ruling 10, landed by design 14 D-3). `end_process` used to sweep the Memory,
    IoMemory and Pipe slabs by charged process and zero every slot, whatever
    another process still held; those three sweeps are DELETED. A dying process
    drops its own references (the close-all) and its quota rows are written off
    in one line — D-4's orphan vocabulary, arriving as the degenerate case it was
    landed in anticipation of — so an ORPHANED OBJECT IS CHARGED TO NOBODY until
    it frees, and a slot outlives its creator for exactly as long as anybody
    holds a reference. The books still balance and the machine stays bounded
    because quota <= wall: the SLAB is what refuses the next allocation. What
    replaced the sweeps is not a sweep by ownership but a reap of what NOBODY
    NAMES — necessary because the close-all deliberately counts without freeing
    (D-5), so deleting them outright would strand a slot whose last handle the
    dying process happened to hold — and it frees through `free_object`, so each
    kind's own credit runs, which the old sweeps never did for a slot another
    process had been charged for. The five counted sweeps stay (their numbers are
    in the teardown line), and Mapping stays with them because its handles carry
    no `Transfer` and so cannot outlive their owner at all.
  - **Pipes and PipeReplyHandle** (§2.1) — M4. **UNITS 1 AND 2 HAVE LANDED THE
    PAIR AND THE ONE-SHOT PAIR (sawos designs 13 and 14), so §2.1 is no longer a
    surface with no implementation: the CONNECTION is an object, request/reply
    is a primitive, and everything polls.** What executes and what is still a
    promise is marked in §2.1's own built-so-far block and in the `Pipe` row of
    §2's table; the headline is that design 10 D-1's role split is real —
    `PipeInlet` and `PipeOutlet` are two counted kinds over one slab row with two
    reference columns, so "the writers went to zero" is the reference ledger
    design 7 already built rather than a second one. `ProcessOp.PipeCreate`
    answers both ends through a copy-out record; `Post`/`Take` are nonblocking,
    with `WouldBlock` for a full or empty ring and `PeerClosed` for a dead peer
    (drain-first on the take side).
    **UNIT 2 REPEATED THAT SHAPE ONE LEVEL DOWN**: `PipeReply` and `PipeRequest`
    are two more counted kinds over one RING SLOT with two more reference
    columns, so a claim and an obligation are ring-slot state rather than a third
    slab — a one-shot exists for exactly as long as one in-flight message, and
    the slot that carried the request carries the reply back. `Post` answers the
    claim, `Take` answers the obligation beside the message, `Resolve` and
    `Reply` each CONSUME the handle they were called through, and ABANDONMENT IS
    READ OFF THE COLUMNS in both directions. The slot now lives until the
    exchange settles, so the in-flight budget bounds awaiting-reply exchanges.
    **AND UNIT 3 LANDED WAITABILITY** (sawos design 15): all four pipe kinds are
    §2.2 waitables, the wait RECORD grew a body region so a reply delivery
    carries its own bytes, `WaiterOp.Add` grew a flags word for the one-shot and
    CONSUMING attaches, and §2.1's ratified blocking `send` / `send(timeout:)`
    became real as library compositions over post + attach + wait plus a Timer.
    One structural consequence worth recording here: `kcore.wake` STOPPED
    EXISTING — a delivery now drops a counted reference (a one-shot attachment
    detaches at its copy-out) and a dropped reference now delivers (a column
    reaching zero raises a peer-gone level), so delivery and the ledger became
    mutually recursive and merged into `kcore.refs`. The module list is one seam
    shorter and the altitude rule that produced it is unchanged.
    What remains is the FUSED compositions `Call` / `ReplyRecv` (unit 3.5),
    handles in messages (unit 4) and the driver-as-service money shot (unit 5).
    **THE M4 PLAN OF RECORD IS `designs/010-m4-pipes.md`**
    (RULED Aug 30, all seven agenda
    items), and it is pointed at rather than duplicated here: §2.1 is carried
    by reference and gains what its built-so-far block records. What that plan
    settles and this section will inherit — a connection is a PAIR of counted
    endpoint kinds (`PipeInlet` client side, `PipeOutlet` server side), so
    "writers went to zero" is the existing reference ledger rather than a
    second one; `post` is the ONE kernel submission primitive and blocking
    `send` / `send(timeout:)` are LIBRARY compositions over post + wait +
    resolve, so the kernel never learns what a timeout is; the factory is
    `ProcessOp.PipeCreate` on its own Process right; and NOTHING PARKED CAN
    BE SILENTLY DOOMED — every "what you are waiting for can no longer
    happen" is a delivered wake with a distinguishable status
    (`SosStatus.Revoked`, `PeerClosed`), whose unit 0 has LANDED ahead of the
    pipes themselves (the entry below).
  - ~~**A parked thread whose Waiter dies**~~ **BUILT M4 UNIT 0 (sawos design
    12)**, and it is the doctrine above arriving before anything that needs
    it. `free_object`'s Waiter arm gained a second last rite: reaching zero
    references now WAKES every thread on the blocked list with the new
    `SosStatus.Revoked`, where M3 left it `Blocked` forever and recorded the
    strand as legal-but-doomed (design 7's As-built finding 2, which the
    ruling answers "wake, don't count"). **NO RECORD IS WRITTEN** — a
    delivery copies out then answers `Ok`, a revocation answers `Revoked` and
    copies nothing, because nothing became ready — which is also why the wake
    lives beside the free rather than in the delivery module: it touches no
    process memory, so it needs neither the copy door nor its altitude. THE
    COUNT IS THE GATE, NOT THE RELEASE (a minted sibling still live frees
    nothing and wakes nobody), and the walk is the WHOLE blocked list rather
    than the pop a readiness does. The husk alternative was weighed and
    rejected; the deadlock predicate gained no arm; the typed surface gained
    none either, since `Waiter.wait` already answers
    `Result<WaitResult, SosStatus>`. One harness row per profile
    (`waiter-revoked`), 160 runs.
  - ~~**Handle close and generations**~~ **THE LIFECYCLE TIER IS BUILT**
    (§3; sawos design 3, M3 unit 2.75). Three mechanisms landed together
    because each alone is broken — mint-per-call without release is a leak
    by design, release without generations is aliasing, and a release op
    beside copyable wrappers is a stale-fault factory. What exists now:
    one UNIVERSAL `RELEASE_OP` intercepted between the table lookup and
    the kind match; a split handle word carrying a per-slot GENERATION
    that survives the unbind, so §3's stale-handle detection is real; every
    getter MINTING a fresh handle; §4's owning `NoCopy` tier over every
    wrapper, so DROP IS RELEASE; and one slab reclaiming on release — a
    `Gone` process's slot, once no handle names it (closing unit 2's pend
    at `alloc_process`, so `MAX_PROCESSES` bounds CONCURRENT processes
    again). What is STILL absent is CLOSE, which is a different act: it
    ends the OBJECT for everyone and exists only on kinds with an end-state.
    **M4 UNIT 1 WAS EXPECTED TO BRING IT AND DID NOT, WHICH IS A FINDING
    RATHER THAN A DEFERRAL** (sawos design 13): a connection END is closed by
    releasing the LAST handle onto it, and that is the reference column
    reaching zero — a fact the ledger already maintains at every bind and
    unbind. A `close` op beside it would be a second way to reach the same
    state, reachable while a sibling handle is still live, and would have to
    answer what a minted sibling then names. So drop-is-release IS the close
    for the kinds M4 has, `let _ = move inlet` is how a program says it, and a
    per-kind close right has nothing left to gate. Whether any later kind
    genuinely needs one is open on its own terms. **THE REFCOUNTED
    RECLAMATION THIS ENTRY DEFERRED IS BUILT (M3 unit 5, sawos design 7
    D-1)**: every countable slab reclaims on the last release now, not
    only a `Gone` process's — and that one arm is no longer special, since
    its handle-table scan became the count every other kind keeps. What
    stays absent from this tier: CLOSE, and per-kind release rights (which
    would be a new universal bit and are explicitly not built). What stays
    absent from the RECLAMATION is stated at §2.5: a freed Memory returns
    its SLOT and not its BYTES, because quotas count objects rather than
    bytes in v1.
  - **Priorities** (§7) — nothing of §7 is built; round-robin was ruled to
    stay through M3 (design 232 agenda item 10) and it did. M4 does not take
    it up either: `designs/010` leaves the §7 band map in its standing tail
    beside SMP, FP-in-userspace and the vDSO true-mapping.
  - ~~**Process waitability**~~ **BUILT M3 UNIT 5.5 (sawos design 8)** — the
    existing Process object became §2.2's fourth waitable, which is what the
    ruling asked for and the whole of what the unit is: no new kind, no new
    right (`ProcessRight.Wait` gates the attach exactly as it gates
    `get_status`), no new status vocabulary (`WaitTag.Process` carries §8's
    own word). **DEATH IS A TERMINAL LEVEL** — a delivery consumes nothing —
    so attach-after-death wakes, a second wait answers again, and supervision
    has no lost-edge race; the attachment is a counted reference (§2's
    counted-kinds table), which is what makes that sound against design 3
    D-3's slot reclaim. The kernel notifies after the status word is recorded
    and before the teardown, and the wake only QUEUES, so a supervisor never
    observes a half-dead process. The deadlock predicate deliberately gained
    no arm: a death arrives from INSIDE the thread set, so a supervisor parked
    on a live child is not idle-and-doomed — the child's own threads are
    runnable — and one parked on a dead child was already woken.
  - **Thread waitability, and `kill`** (§8) — attaching a THREAD is still a
    `NotWaitable` fault (and so is attaching a MemoryObject, which
    has no state that could become ready); `kill` still has no op and no
    right. A supervisor may now learn a child died either way: by READING
    `get_status` through the handle it holds, or by being WOKEN through an
    attachment on the same handle.
  - **`IntrSpinLock` (§9b), SMP** — still unbuilt, and correctly so: a
    preemption point IS the assertion that state is consistent, so
    nothing yet has a critical section for the type to protect. SMP
    waits for pipes. **Kernel interruptibility itself is BUILT** (sawos
    design 1, M3 unit 1.5): design 178's D2 holds in its sharpened form —
    the hardware never delivers in kernel mode, and the kernel asks at
    named points inside long operations. §9 records what that amended.
  - **`HandlerGroup`** (§10) — userspace runtime work rather than kernel
    surface, and unbuilt: a process wires a Waiter by hand today.
  - **DMA CONFINEMENT — the IOMMU driver and CRITICAL PROCESSES** (§2.5's
    TCB note, ruled sawlang#178 round 4). Nothing of it is built and nothing
    in the tree needs it yet: a `map` fences a driver's CPU and not its
    device's bus mastering, so a DMA-capable device's driver is a TCB member
    until an IOMMU exists, and both v1 boards' consoles are programmed-I/O.
    Of the two prerequisites the ruling named, M3 landed the first —
    process-death notifications (§8, unit 5.5) — and the second, a process
    root marks CRITICAL whose exit for any reason faults the system, is
    unbuilt. M4+ (`designs/010` explicitly excludes the IOMMU driver from
    M4 and lists it as death-notification consumer 2).
  - **The mapped vDSO** (§5.7) — delivery is still static linking, which
    changes nothing about the shape.
- **Roadmap (brief numbers assigned at dispatch — the design-78/79
  references in §5b are stale). PATH NOTE, since the entries below name
  directories: the tree FLATTENED at the sawos split (sawlang#238 D-e,
  Aug 28) — what these entries built as `sos/kernel/`, `sos/hal/`,
  `sos/rt/` and `sos/root/` are `kernel/`, `hal/`, `rt/` and `root/` in
  this repository, and every path in this document is spelled the way the
  tree spells it today (corrected wholesale at M3 unit 7).** M0 riscv32
  QEMU target (design 112) →
  M1 riscv32 QEMU boot-to-root-server → M1b arm64 EL1 parity + HAL
  extraction → M2 the concurrent kernel, landed once and two-profile
  tested → M3 the multiprocess kernel (design 232, run as sawos designs
  1-9 and CLOSED by design 11, this sweep) → M4 pipes (`designs/010`,
  RULED Aug 30 — the plan of record; seven units from waiter revocation
  to driver-as-service).
  - **M0 DONE (design 112):** Profile A substrate is live —
    `kernel/` (boot.S + virt.ld + rt.c runtime seams + a Saw `main.saw`
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
    behaviour), and `kernel/main.saw` no longer prints-and-exits — it loads
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
    (`kernel/sysapi/`), every number lives in one kernel-internal package
    (`kernel/abi/`) shared by the dispatch tables and those wrappers, and a
    process links `sos` and never writes a number. A per-op C-ABI surface
    (`sos_system_debug_print`, `sos_system_shutdown`) sits beside the Saw one
    over a fixed-arity raw `sos_syscall1` over the per-arch stub — one chain,
    three altitudes. The §6 boot protocol is
    concrete: a flat **sosimg** (16-byte header + 20-byte segment records, all
    fixed-width little-endian per design 47, carrying the §7 priority map),
    emitted by a Blade `emit = "sosimg"` build target reading the package's
    `[sos]` manifest section, appended to the kernel image as a `.payload` blob
    with linker-symbol bounds. The layout lives ONCE, in the shared
    `libs/imgformat/` package: Blade consumes it as a path dependency and emits
    bytes through it, the kernel consumes it through `--module-path` and reads
    images by overlaying the same structs as `UnsafeMemory` typed views, with
    `static_assert` pinning the sizes on both sides. `root/` is a real
    separate Saw package built by Blade; it prints its banner THROUGH a System
    op — it holds no device grant and could not print any other way — and calls
    `shutdown(0)`. The kernel validates an image in full before placing a byte
    of it, so a segment aimed at the kernel is refused rather than obeyed.
    `make sos-test` is 11 cases including the two-image boot, a root that
    oversteps its grant, and one that makes bad calls and checks the statuses.
    Structure: `kernel/core/` is shared by every kernel image, which
    keeps them all on the same trap path — one module per seam since the
    Aug-17 split, with `lib.saw` the facade that re-exports what an image
    entry names; `rt/common/` (Saw — since design
    172 part 2 the runtime seams and the arena too) and
    `rt/common_c/support.c` (the C that must stay C: `mem*` and the atomic
    libcalls) are shared by the
    kernel and every process; and the architecture lives in
    `hal/riscv32/{kernel,user}/`, each with an ABI.md, so M1b (design 162)
    ADDS `hal/arm64/...` without moving any of it.
  - **M1b DONE (design 162), branch PARKED for user review:** arm64 EL1
    parity and the HAL extraction. The M1 feature set above runs identically
    on `qemu-system-aarch64 -M virt -cpu cortex-a53`: twelve cases per
    architecture, twenty-four total, either failing red. The M1 claim that
    "the architecture lives in `hal/`" turned out to be half true — the
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
    `timer_rearm`/`timer_pending`, and a selftest line) — the timer trio was
    reshaped by M3 unit 1, see that entry. `ktrap` decides
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
    out (`SystemOp`/`ProcessOp`/`ThreadOp` beside `SystemRight`/…; ruled
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
    encoding is API (`event_create(mode:)`), the wait ANSWER became a
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
  - **M3 IS DONE (design 232, run in sawos designs 1-9 and 11 — design 10 is
    M4's sketch, not a rung) — units 1, 1.5, 2,
    2.75, 3, 4, 5, 5.5, 6 and 7 all landed, and unit 7 is what closes the
    milestone.** THE LEDGER, one line per rung, each naming the sawos
    design that ran it: unit 1 the Clock and Timer (design 232's own unit,
    parked before the split); unit 1.5 kernel interruptibility
    (`designs/001`); unit 2 the second process (`designs/002`); unit 2.75
    the handle lifecycle — release, generations, mint-per-call and the
    owning `NoCopy` tier (`designs/003`); unit 3 `give` and `MINT_OP`
    (`designs/004`, with the sysapi split at `designs/005`); unit 4 Memory,
    IoMemory and Mapping (`designs/006`); unit 5 the reference count and
    quotas (`designs/007`); unit 5.5 death notifications (`designs/008`);
    unit 6 the driver child and `MemoryRight.MapExecute` (`designs/009`);
    and unit 7 this docs sweep (`designs/011`), which rewrote §3 around NO
    AMPLIFICATION, amended §5.7 and §12, recorded the DMA-TCB bound at
    §2.5, refreshed this section, and moved no transcript row — a docs unit
    that moves one has changed something it claimed not to. **THE GATE AT
    M3'S CLOSE IS 79 CASES PER ARCHITECTURE, 158 RUNS**, riscv32 and arm64
    both, either machine failing red; eleven object kinds where M2 had six,
    with Pipe the one row of §2 left and M4's (`designs/010`). The finale is
    unit 6 (`designs/009-driver-child.md`): **A DRIVER IS A CHILD
    PROCESS.** Root drains the console UART's register page out of its own
    boot set as an `IoMemory`, GIVES it to a child, and the child maps it
    into itself, binds the line, enables the device and echoes the
    harness's bytes — on both profiles, with a driver body byte-identical
    to the root-as-driver twin's. That one transcript spends every rung of
    the ladder at once: the second address space (unit 2), the handle
    lifecycle (2.75), give and the masked System handle (3), IoMemory and
    `map` (4), the reference count and quotas (5), and a supervisor woken
    by the death rather than by a guessed deadline (5.5). Beside it the
    unit landed SHARED MEMORY — one region in two address spaces, the
    launcher installing the child's row, the child holding no region
    object — and, on an Aug-30 ruling, EXECUTABLE AS A RIGHT
    (`MemoryRight.MapExecute`, with `Write | Execute` in one row refused);
    §2.5 carries both. Eight new harness rows per the two profiles, 158
    runs, with the 150 existing rows unchanged.
    Unit 2 is
    `designs/002-create-process.md`, and it is the one the milestone is named
    for: **SOS RUNS TWO PROCESSES.** `process_create(image:memory:)` takes two
    Memory capabilities and returns an INERT process; `start()` mints its
    first thread and lets the scheduler pick it up while the caller keeps
    running. Nine object kinds where unit 1 had eight; five new harness cases
    per architecture, 47 each, 94 runs, with the 84 existing rows unchanged.
    What the implementation added to §2, §8, §11 and §12:
    (a) **THE LOADER STAYS IN THE KERNEL** (user, Aug 28, superseding the same
    day's zero-copy ruling), and it is ONE loader with two doors: the phases
    take the destination as a region parameter and differ only in whether a
    refusal is `fatal_image` or a `BadImage` status. §12's loader-above-boot
    rule is exercised without a second code path existing anywhere.
    (b) **PROTECTION IS RELOADED AT THE SWITCH, forced on both profiles.** One
    has a small fixed budget of numbered regions that a three-segment root
    spends entirely; the other's grant window is a single shared user-mode
    permission map. So a process's rows are DATA in its slot, replayed by the
    scheduler when the incoming thread's process differs from the installed
    domain — and a same-process switch, which is every switch every earlier
    case makes, reloads nothing. That is what kept the shipped transcripts
    byte-identical.
    (c) **A PROCESS'S DEATH IS A SCHEDULING EVENT.** `end_process` forks: root
    stops the machine exactly as before, a child leaves the ready queue BY
    REMOVAL (the wholesale zeroing was a one-process spelling that would have
    taken root's runnable threads) and the path ends in the scheduler. That
    forced the teardown UP a module — the reschedule can idle, idling
    delivers, and delivery reaches back down into the copy door — which is
    design 1's cycle at a second site, resolved the same way: the doors REPORT
    a bad buffer and the callers above the switch point terminate on it.
    (d) **THE BUILD EMITS A REGION TABLE; THE KERNEL INTERPRETS NOTHING**
    (sawlang#232 agenda item 2's ruled hybrid). The stitcher appends each
    child image as it is and records `{base, len}` rows in a new fixed-symbol
    section; the kernel mints one sealed MemoryObject per row and hands them
    to root through `BootHandleNext`; root's config is what says which ordinal
    is which. A missing table is zero regions, which is why every image built
    without children boots unchanged.
    (e) **THE MONEY PROOF IS AN ABSENCE.** A child stores into root's region,
    the access faults, the child dies and root says so — and a kernel with a
    broken reload fails by the fault report going MISSING rather than by a
    wrong value, which is the failure mode worth engineering for.
  - **Unit 1.5 DONE** —
    the first sawos-native design (`designs/001-kernel-interruptibility.md`,
    implementing 232 pin 1) and the first unit to land in this repository
    rather than in sawlang: **a long kernel operation is interruptible.**
    Design 178's D2 sharpens rather than repeals — the hardware still never
    delivers an interrupt in kernel mode, and the kernel now ASKS at named
    preemption points, polling and delivering through the funnel the idle
    path already proved. The mechanism is three pieces: `preempt_point()`
    beside the idle poll, one reentrancy guard around the delivery funnel's
    body (the byte movers are shared with IRQ context), and a 4 KiB stride
    on the long-op movers every bulk copy goes through. Every kernel-mode
    loop carries a one-sentence placement verdict at its site, and the
    console paths' verdict is an ACCEPTED latency rather than an argued-away
    one. Two new all-arch cases invert the two D2 witnesses — a tick taken
    in kernel mode, and a device line serviced before the entry to user
    mode — 42 per architecture, 84 runs, with the 80 existing rows
    unchanged. §9 and §9b carry the amendments; `IntrSpinLock` stayed
    correctly unbuilt, because a point IS the assertion that state is
    consistent. One structural finding, recorded in the design's as-built:
    a point delivers, so it sits ABOVE the byte loops it drives in the
    module order while delivery reaches back down to them, and the
    resulting cycle is why the cadence is its own module (`kcore.preempt`)
    rather than a point written inside `copy_bytes`.
  - **Unit 1 DONE, branch PARKED for user
    review:** the Clock and Timer objects, and with them the thing SOS could
    not do before — **a process can sleep**. Eight object kinds where M2 had
    six; eight new harness cases per architecture, 40 each, 80 runs. What the
    implementation added to §2's two new rows:
    (a) **ONE HARDWARE TIMER, TWO CUSTOMERS.** Both machines have a single
    comparator, and the scheduler tick (§7) and every armed Timer now want it.
    The per-arch seam flipped from PERIODIC (`timer_start(period_us)` +
    `timer_rearm()`) to ABSOLUTE (`timer_now_ns()` + `timer_set_deadline_ns(at)`)
    — a periodic seam can express exactly one customer — and the tick's PERIOD
    moved out of the HALs into the arch-free kernel, where scheduler policy
    belongs. The kernel keeps every deadline it owes in nanoseconds and programs
    the EARLIEST; a fire means "something MAY be due", and one reading of the
    clock answers for every customer with the TICK ASKED FIRST. **§7's tick is
    thereby inviolate by construction**: a Timer deadline can only ever move the
    hardware earlier, never later.
    (b) **THE TWO RE-ARM RULES ARE OPPOSITE, deliberately.** The tick re-arms
    from NOW (a late tick is one tick, never a burst of catch-up), a Timer from
    its own DEADLINE (drift-free, the timerfd model) — a timeslice has no
    history to be faithful to and an interval does. Missed expiries COALESCE
    into a saturating count by DIVISION rather than by a loop, so an interval
    far below what the machine can serve costs one divide; what keeps it from
    livelocking is a floor on how soon the hardware is ever programmed, which
    leaves the logical schedule untouched. No minimum interval is imposed and
    nothing is refused.
    (c) **The copy-IN funnel**, §2.2's copy-out door run backwards, built here
    for `Timer.Arm` and inherited by M4's message body.
    (d) **"Nothing runnable" split again, and the rule GENERALIZED.** M2 made it
    "idle iff a line is bound"; an armed Timer is readiness the clock will
    deliver, so the rule is now "idle iff something outside the thread set can
    still wake somebody". An unarmed Timer is NOT one — it will never fire — and
    that distinction has its own case, because getting it wrong that way fails
    as a harness timeout rather than as a report.
    (e) **Two language findings, neither worked around** (DF-232a, an internal
    compiler error on a bare literal assigned to a fixed-width place; DF-232b,
    `type` was a keyword so the ruled `clock_get(type:)` label was unwritable
    and shipped as `kind:`. `type` became CONTEXTUAL Aug 17 and the label reads
    `type:` as ruled).
    (f) **THREE REVIEW RULINGS (user, Aug 17), all on this branch.** (i) An
    EVENT'S WORD IS CONSUMED ON DELIVERY: a wait record reads and clears it in
    one interrupts-masked step, so a value is reported exactly once, and
    `receive` keeps its exact semantics as the non-blocking poll — the two are
    doors onto one value (§2.2, §2.4). Two new harness packages, split by door.
    (ii) A TIMER'S VERBS ARE METHODS ON ITS SLOT, which pulled the drift-free
    arithmetic out of the `unsafe` bodies that index the slab and into safe ones
    — structure, with no contract moved. (iii) THE HARDWARE CLOCK IS A GLOBAL
    SINGLETON per `ClockType`, kernel-eternal and owned by nobody, so the slab is
    one slot per domain and teardown frees none — the §2 Clock row has it.

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
  `InterruptBind`, so binding a line is a derivation through a handle
  it already holds rather than a table it is given — and its device
  window arrives in its own image (§2.5), not in the boot set. **ONE OF
  THE TWO BECAME AN OBJECT IN M3 AND THE OTHER DELIBERATELY DID NOT**
  (corrected at M3 unit 7; §5.7's M3 amendment states the rule): the
  pool roots are `Memory`/`IoMemory` capabilities now, delivered through
  the boot set as the paragraph below describes, while the root IRQ
  TABLE stayed a RIGHT — `ProcessRight.InterruptBind`, a derivation
  through a handle root already holds rather than a table it is given.
  Neither widened the register the kernel enters user mode with.
  **AND THE POOL ROOTS ARE DELIVERED (M3 unit 4, sawos design 6 D-5).**
  This bullet's "root MemoryObjects — the RAM pool root and the
  device-range roots" now exist as written: the build-emitted region
  table carries a KIND COLUMN, a Ram row mints a `MemoryObject` and a
  Device row an `IoMemoryObject`, and both arrive through
  `BootHandleNext` under their row ordinal. **THE SET IS STILL ONE
  HANDLE WIDE AT THE REGISTER** — pool roots are COLLECTED, not passed,
  which is the property that has survived every kind added to the set —
  and the DEVICE ROOT is what retires the M2 "arrives in its own image"
  clause above: a driver now obtains its window instead of declaring it,
  which is what makes handing one to a driver CHILD possible at all.
  What the kernel still assigns no meaning to is which ordinal is which;
  that is root's config, exactly as it has been since unit 2.
  NOTE (object-model brief material): the creation-authority model for
  plain objects (Pipe/Event/Waiter/Timer) — quota-gated free
  creation vs a factory capability — is an open pin; M1 does not need
  it (root spawns nothing). **ANSWERED BY M2 (design 178 unit 3): the
  factory capability, spelled as rights bits on the Process object.**
  `EventCreate`/`WaiterCreate` — and `InterruptBind` beside them — are
  Process ops each on its own right, so creation flows through a handle
  the process already holds and is §3's derivation rule rather than a new
  mechanism next to it, and a launcher strips the bit from anything with
  no business creating. A quota stays ADDITIVE: a field on the process
  slot, checked where `NoResource` is returned today, and design 232
  unit 5.
  **AND THE QUOTA IS BUILT, ADDITIVE EXACTLY AS PROMISED (M3 unit 5,
  sawos design 7 D-3).** Not one rights bit moved and not one op was
  added: the factory capabilities still decide WHETHER a process may
  create, and the quota decides HOW MANY — checked at the sites this
  bullet named, ahead of the slab, answering `SosStatus.QuotaExceeded`
  where the machine would have answered `NoResource`. The one
  spelling that differs from the promise is WHERE the field lives: the
  rows are per-process storage in `kcore.objects`, beside the handle
  table, rather than on `ProcessSlot` — because the HANDLE row (design
  3's promised addition, and the one kind whose "slab" is a region of a
  per-process table) has to be charged inside `mint_handle` and credited
  inside `unbind_handle`, and the process slab sits above that module.
  `ProcessSlot`'s docstring points at it.
  **AMENDED Aug 29 (user ruling; sawos design 2's RIDER) — THE M2 ANSWER
  STANDS FOR PROCESS-SCOPED OBJECTS, AND A PROCESS IS NOT ONE OF THEM.**
  The sharpened line: process-scoped objects are minted by your Process;
  PROCESSES ARE MINTED BY THE SYSTEM. Threads, Events, Waiters, Timers
  and Interrupts are a process's own — charged to its slab slots, its
  handle table and its teardown — so their creation stays a rights bit on
  the Process object exactly as ruled in M2. The process TABLE is a
  machine-global resource bounded by machine facts (the slab, the
  protection-domain reloads, ASIDs when Profile B grows real
  translation), so its refusals and its limits are SYSTEM answers:
  `process_create` is `SystemOp.ProcessCreate` gated on
  `SystemRight.ProcessCreate`, and `SystemOp.ProcessSelf` was already the
  precedent — Process handles have always come from System, and now they
  all do. Two arguments carried the ruling. The topology one is the
  sharper: with a root/launcher creating every process, a creation right
  on the CALLER's own Process handle gates nothing real, and creator-pays
  accounting through the caller's slot would attribute everything to
  root — so attribution in that world is ASSIGNED BY POLICY (unit 5's
  quota vocabulary), not derived from who called.
- **Loader-above-boot.** The kernel loads exactly ONE image ever: root
  (sosimg, §6). Every later process is loaded BY ROOT from images root
  obtains itself (e.g. a flash MemoryObject it holds), via LAUNCH +
  `create_process`. The kernel has no second code path for process two.
  **EXERCISED, M3 unit 2 (sawos design 2 D-1), AND THE KERNEL STILL HAS ONE
  LOADER.** Two rulings of Aug 28 settled the shape and the second superseded
  the first: zero-copy link-in-place was ruled and then overturned — **the
  loader stays in the kernel entirely**, doing the copying, with a userspace
  split deferred until it is necessary. So the kernel parses sosimg at boot and
  at `process_create` through the SAME phases: `validate_image` and
  `place_image` take the destination as a REGION PARAMETER (`LoadRegion`), and
  the only thing that differs between the two doors is the FAILURE VOCABULARY —
  the boot door raises `fatal_image` because a machine whose only image is
  malformed has nothing else to run, and the create door returns `BadImage`
  because a launcher must be able to report what it was handed. "No second code
  path" is therefore kept BY CONSTRUCTION rather than by discipline: there is
  nowhere else in the tree that reads a segment record.
  **ROOT PROVIDES THE MEMORY AND SUPPLIES NO NUMBERS** (ruled Aug 28, via the
  stack question): `process_create(image:memory:)` takes two Memory
  capabilities, segments are copied to their LINK addresses inside `memory`,
  and **the stack is the kernel's grant at that region's top** — root's own
  loader rule, applied to a child. So a launcher never parses an image, never
  computes a load address and never chooses a stack, which is what makes "root
  never parses" airtight rather than merely intended.
- **THE BOOT SET IS COLLECTED, NOT WIDENED** (M3 unit 2, sawos design 2 D-3).
  The entry register still carries exactly ONE handle — System — and everything
  else the kernel minted for root before it ran is drained through
  `ProcessOp.BootHandleNext`, one `{tag, kind, handle}` record per call, through
  §2.2's existing copy-out funnel. Exhaustion is a STATUS (`Drained`) that stays
  exhausted, because a loop that drains an iterator ends by meeting the end. The
  TAG is a region ordinal out of the build-emitted table and the kernel assigns
  it no meaning: which ordinal is an image, which is a destination, which is a
  device is ROOT's config. That is what keeps the kernel's half of the launch
  flow identical for a hand-built M3 system and for M4's dynamic loading.
- **AND EVERY PROCESS HAS ONE** (M3 unit 3, sawos design 4). The boot set went
  PER-PROCESS when there were two drainers: the kernel writes root's at boot and
  a LAUNCHER writes a child's with `ProcessOp.Give`, through the same machinery,
  and a child drains its own through its own Process handle exactly as root
  drains root's. The two producers are indistinguishable on purpose — the kernel
  is the giver nobody gave to — and the drain is one op with one exhaustion rule.
  Three sentences carry the model:
  - **THE TAG IS THE IDENTITY, and tags are the ONLY cross-process vocabulary.**
    A tag is the giver's own word handed back unread (the `Waiter.add` key
    precedent): the kernel is a courier, never an interpreter, and a launcher and
    a child agree on meaning through config and manifest. So a give answers with
    a STATUS ALONE — the child-side word means nothing to the giver, which can
    call no op through it — and a DUPLICATE tag is refused at the give, because
    an identity naming two handles would make the boot lookup ambiguous.
  - **THE SET FREEZES AT `start`.** Whatever a launcher gives must be complete at
    the child's first instruction, and the start call is the barrier that makes a
    half-populated table unrepresentable with no synchronization invented; a give
    afterwards is a `BadState` fault. The DRAIN has no deadline at all — records
    persist until consumed, so a child that drains late drains correctly.
  - **`start(boot_tag:)` IS HOW THE FIRST HANDLE ARRIVES.** The kernel resolves
    the tag to the child-side word and puts it in the first argument register, so
    `_start(boot_handle)` is unchanged from M2 and a launcher never sees a
    child-relative word at any point. Resolution consumes no record, so the child
    meets that handle again in its drain. The no-tag form is the sandboxed
    compute process and is still the default; a tag naming no given record is a
    `BadArg` fault, and "no tag" is a FORM argument rather than a reserved tag
    value, because zero is a legitimate tag (root's region ordinals start there).
  **AND A CHILD BOOTSTRAPS EXACTLY AS ROOT DOES** (the Aug-29 rulings; §3's
  `MINT_OP`). A launcher MINTS a masked sibling of its own System handle — a keep
  mask naming `Debug | ProcessSelf | ClockGet | Transfer` and nothing else — gives
  the sibling under a tag, and starts the child naming that tag. The child then
  does what root does at boot: adopt the System handle in its first argument
  register, derive its own Process object from it, drain its boot set from there.
  So the kernel has ONE way in for every process, which is the same claim
  loader-above-boot makes about images, and the differences between root and a
  child are entirely in a rights word.
  What that buys, and it is the whole point of the mint: the launcher KEEPS the
  child's Process handle, so it supervises (`get_status`, and unit 5.5's
  death-wait) while the child manages itself. Before `MINT_OP` there was one
  Process handle per child and a launcher had to choose. What the mask withholds
  is enforced at run time by the ordinary rights check — a child that asks to
  `shutdown` is `AccessDenied` and dies, and the machine keeps running.
  **AND WHAT A LAUNCHER FURNISHES REACHED HARDWARE** (M3 unit 6, sawos design 9
  D-2). The furnishing vocabulary did not grow a mechanism — it grew a third
  `give` funnel, `give(iomemory:)`, over a bit `iomemory_rights()` has minted
  since unit 4 — and with it a launcher hands a child a DEVICE. That is this
  section's design test taken literally: splitting the launcher's job out of
  root required zero kernel changes, and so did moving a driver out of root,
  because a device window was already a capability rather than a manifest key.
  Two shapes are now both ordinary and a launcher picks by policy: GIVE the
  capability and let the child install its own row (the driver child), or KEEP
  the capability and install the child's row yourself (`share_double_map`'s
  shared page, where the child holds no region object at all). Attenuation runs
  the same way on either: `Memory.mint(rights:)` narrows a region before it
  travels, which is where a launcher writes "this child may map its RAM and may
  never map it executable" (§2.5's `MemoryRight.MapExecute`).
- **v1 protocol conventions** (userspace convention section, not kernel
  surface): a launched process receives ONE bootstrap pipe handle at
  launch; its first messages request its initial handle set from the
  launcher (name-service discovery = ask by string name over that
  pipe). Conventions are versioned in the `sos` userspace module,
  not in the kernel.
