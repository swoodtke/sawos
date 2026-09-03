# SawOS design 21 — the money shot: driver-as-service (M4 unit 5)

Ruling 7(a) (designs/010, Aug 30, confirmed): the M3 driver child
grows a PIPE-SERVER face and a SIBLING CLIENT reads the UART through
the pipe protocol instead of registers — the namespace's /dev/uart0
model with the paths still to come (M5). TELL and CANCELLATION ride.
This unit CARRIES THE MAX_PROCESSES QUESTION to the user (root + two
children forces it) and lands the THREE-PROCESS DELEGATION spelling
unit 4 deferred on exactly that answer. Dispatches AFTER design 22
lands (its cases are written to ruling 12's surface: error-channel
take/post, `(move claim).resolve()`, `ready()`).

## The ruled surface (cited)

- #10 ruling 7(a): driver-as-service confirmed.
- #9 D-2 (the M3 driver child as it stands): child owns the UART via
  given IoMemory, binds its IRQ, maps its own window, echoes.
- #18: `pipe-delegate-msg`'s header — "the three-process spelling
  waits on unit 5's topology and the MAX_PROCESSES bump it forces."
- #10 ruling 4: TELL is post-then-drop. §2.1's request-abandoned
  early notice (the request's Wait level, unit 3) is the
  cancellation mechanism — nothing new is built for either.
- #17/#18's ladder end state: the driver serves as a
  COMPLETION-QUEUE server (persistent-consuming outlet attach,
  wait + reply_wait, one trap per message) — the money shot runs the
  ladder's best shape, not its first.

**AMENDED (user, Sep 2, at review): MULTIPLE CLIENTS — root is a
client too.** Root mints a `PipeInlet` SIBLING, gives one inlet to
the client child and RETAINS one, and after launching both children
drives the same READ/WRITE protocol itself. Two clients on one
connection: sibling inlets are per-client authority (exactly why
endpoints kept `Mint` under ruling 12 while the one-shots lost it),
and each request carries its own reply obligation, so the driver's
completion-queue loop routes every reply to the right claim with no
new mechanism — that routing is now part of the proof.

## Topology

Root creates the pipe pair and BOTH children, then becomes a CLIENT
itself (the amendment above):
- **driver child** (the uart-echo lineage, per arch): given the UART
  IoMemory (Transfer spent, maps its own window), its IRQ bind, a
  masked System (`Debug|ProcessSelf|ClockGet`), and the pipe
  OUTLET — which it attaches persistent-consuming and serves via
  `wait`/`reply_wait` forever.
- **client child**: given ONE of the sibling INLETS and the same
  masked System. It holds NO device authority — every byte it sees
  or sends moves through the protocol. That asymmetry IS the
  demonstration.
- **root**: retains the OTHER sibling inlet and runs the same
  protocol after launch — supervisor and client at once, no extra
  process slot spent.

## The protocol (§API — the review gate's subject, userspace-only)

No kernel/abi or sysapi surface in this unit. The protocol is a
`PipeMsg` schema, one op byte then payload, defined in ONE shared
module both children import (placement: a small package under
tests/, the agent argues the spot):

- `READ` (Call): request = op byte. Reply = the next input byte(s),
  length-prefixed by the reply's own `len`. If no input is pending
  the driver HOLDS the request (parks it in its pending set — the
  obligation is transferable state, §2.1) and replies when the IRQ
  delivers a byte; the client's fused `Call` parks meanwhile — a
  blocking read composed of shipped parts.
- `WRITE` (TELL): request = op byte + bytes; posted then dropped —
  no reply, no claim held. The driver writes them out its window.
- CANCELLATION: a client that gives up a pending `READ` drops its
  claim (or times out via its own waiter + Timer — kernel still
  never learns what a timeout is); the driver's request-abandoned
  level fires and it discards the held obligation. The ratified
  early notice, exercised in a real server loop.

## The question carried to the user: MAX_PROCESSES

Today `MAX_PROCESSES = 2` (kcore.limits — "raising this number is
the only edit," and M3 unit 2 verified that held). Root + driver +
client = 3. The facts for the ruling:
- A slot costs `MAX_HANDLES` handle entries, a grant-record table,
  and a term in the handle wall — arithmetic, no new mechanism.
- The PMP budget is PER DOMAIN (8 TOR regions = all 16 entries,
  reprogrammed at `load_domain`), so the bump does not touch it —
  but ROOT'S OWN 8 rows during a two-child launch (its segments +
  stack + two staged child images + pool splits) are the squeeze to
  WATCH; the agent reports the row census at peak wiring, and if
  root runs out that is a STOP-and-park, not a workaround.
- **RULED (user, Sep 2, at review): 3.** The smallest number that
  serves the money shot and the deferred three-process delegation —
  and the multi-client amendment costs nothing here, since root
  clienting through its retained inlet spends no process slot. The
  Aug-31 soak-case idea stays parked; nothing here precludes a
  later bump.

## The proof (harness)

1. **`uart-service-<uart>`** (per arch, beside the unmoved M3
   twins): the money shot — TWO CLIENTS in DETERMINISTIC PHASES
   (the sequencing enforced through the protocol itself, never the
   scheduler): the client child echoes the first stdin phrase
   through READ Calls / WRITE TELLs, then root drives its own round
   through its retained sibling inlet. One arm pins TWO outstanding
   READs (order fixed by the phase protocol before the input bytes
   arrive) and proves each reply lands at ITS OWN claim — the
   multi-client routing witnessed, not assumed. The echo after the
   handover marker can only happen if the pipe protocol, the IRQ
   path, and the process boundaries all work. Trap accounting
   printed: the driver's serve loop at one trap per message (the
   ladder's end state, measured in situ).
2. **`uart-cancel`**: a READ posted with no input pending, the
   client abandons (waiter + Timer), the driver's abandoned-level
   fires and the loop serves the NEXT request cleanly — one dead
   read cannot wedge the driver.
3. **`pipe-delegate-3p`**: unit 4's deferred spelling made real —
   the client's request forwarded THROUGH a middle process in a
   message, the far process replies, the reply lands at the
   original claim across three processes.
4. Existing transcripts UNMOVED except rows that print
   MAX_PROCESSES-derived numbers (the wall assert, process_stats
   headers if any) — each such row accounted authorized-with-cause.

## Docs owed

spec.md: the driver-as-service story lands in the §2.1 narrative
(the delegation example now three-process real); MAX_PROCESSES'
comment records the bump and its forcing unit; the limits doc-block
census. Tracker entry closed in place; As-built here; SL-N entries
for genuine deficiencies.

## Out of scope

Namespaces/paths (/dev/uart0 stays a model, M5); the soak case
(unless ruled in with a 4+ bump); priorities; the M4 docs sweep
(unit 6); any `hal/riscv32` edit beyond what the row census itself
requires — and the esp32c3 sibling directory is design 20's, not
this unit's.

---

# As-built (M4 unit 5, Sep 2 2026) — SHIPPED

Base `d88def5` (the design-22 integration). **EVERYTHING THE BRIEF ASKS FOR
LANDED, IN THE SPELLING IT ASKS FOR, AND THE KERNEL DELTA IS ONE LINE.** The
gate is green on both arches — 116 cases each, 232 assertions — and the only
number this unit changed in the kernel is `MAX_PROCESSES`.

**THE UNIT'S CLAIM, STATED ONCE**: driver-as-service needed NO kernel surface.
No op, no object kind, no right, no status, no slab, no ABI value. A blocking
read is a fused `Call` meeting a held one-shot; a one-way write is the ratified
TELL; a cancellation is a dropped claim seen through the request's abandoned
level; two clients on one connection are two minted sibling inlets. Every one of
those was shipped by units 1 through 4.5, and unit 5 is the program that spends
them.

## The money shot, verbatim (riscv32; the other profile differs in its chip)

```
SOS M1: kernel up on riscv32 (QEMU virt)
SOS: root image ok segments=0x00000002 entry=0x802026e6 prio=0x01010100
SOS: boot regions=0x00000005
SOS: console handover
SOS uartsvc: created
SOS uartsvc: driver started
SOS uartsvc: read posted
SOS uartsvc: client started
Zq7#
SOS: process exit: code=0x0000003d process=0x00000002
SOS: process teardown handles=0x00000003 threads=0x00000001 ... process=0x00000002
SOS uartsvc: root wrote this line through the driver
SOS uartsvc: and root does not hold the device
SOS uartsvc: root's claim answered byte 90
SOS uartsvc: client traps=13 for 3 reads and 3 writes
SOS uartsvc drv: served=13 abandoned=0 dropped=0
SOS uartsvc drv: after the mark answered=2 discarded=2 traps=7
SOS uartsvc drv: one trap per answered message, two per discarded
SOS: process exit: code=0x00000000 process=0x00000001
SOS: process teardown handles=0x00000002 threads=0x00000001 ... process=0x00000001
SOS uartsvc: the driver served two clients and ended clean
SOS uartsvc: done
```

(The blank lines the real console carries between the `print` rows are omitted
above; freestanding `print` appends a newline to a format string that already
ends in one, which is tree-wide and predates this unit. `debug_print` does not,
which is why the launcher's own markers are tight.)

`Zq7#` is the harness's four typed bytes coming back out, and **the FIRST of
them travelled through a different process from the other three**. Root read
byte 0 through the claim it kept and echoed it; the client child read bytes 1-3
through a minted sibling inlet and echoed those. The two teardown rows in the
middle are the client's, and they are the estate this unit is about: THREE
handles — a masked System, the Process it derived, and one pipe inlet. No
`IoMemory`, no `Mapping`, no `Interrupt`. A process that reads a UART and cannot
name one.

## What landed, by area

**`kernel/core` — one line of code and four doc-blocks.** `limits.saw`:
`MAX_PROCESSES` 2 -> 3, with the census the bump owes written at the constant
(what a slot costs, what derives, and what does NOT — the protection-row budget
is per DOMAIN); `MAX_ATTACHMENTS`' doc-block gained the note that it moved
22 -> 23 by arithmetic rather than by an edit, plus the argument for why the
pipe waitables are deliberately not terms; `MAX_PIPES`' "unit 5 will argue for
more" sentence was answered — it did not, because a service's connection count
tracks SESSIONS and not clients. `sched.saw`: three invariant notes that rested
on `MAX_PROCESSES == 2` were rewritten, and the rewrite is the honest part of
the bump. Two of them said an arm was UNREACHABLE and gave the table's size as
the proof; at three, a child can supervise a sibling and a client child does die
while its sibling driver keeps serving. The arms are still untaken — root
creates every object those two share and outlives both — but they are untaken by
TOPOLOGY now, which is the difference between a branch nothing takes and a
branch nothing can reach. That is exactly why ruling 10 deleted the force-free
arms instead of leaving them.

**`tests/uartproto` — the protocol, as a LIBRARY package.** The brief asked the
agent to argue its placement. It is a Blade library under `tests/`, and the
argument is three sentences. (1) It is not kernel surface: the brief forbids
growing `sos` or `kernel/abi` for this unit, and the reason is the design rather
than the scope — what the kernel supplies is the connection, the message and the
one-shot pair, and it supplies those to every protocol equally; a device's
protocol belongs to the device's driver and its clients, which is what M5's
namespace will give a path to. (2) It is not `sosrt`: that package is the SOS
RUNTIME — arch-free, role-free code every image links — and a UART protocol is
neither. (3) It is a PACKAGE and not a comment because four programs compile
against these numbers (the driver, the client child, and each root launcher
acting as a client of its own) and the whole claim of the unit is that they
AGREE; a copy per program is a copy that can drift, and the drift would surface
as a wrong byte rather than as a compile error. That is `PIPE_BODY_BYTES`'
argument one altitude down.

Contents: `UartOp` (a raw-backed wire enum — `Read`, `Write`, `Sync`, `Mark`),
`UartBody` (the request builder), and three decode helpers. `op_of` is the one
Optional in the surface and it is a real domain value, not a transient: a
request arrives from a process the driver does not trust, so "this byte names no
op" is what `from(raw:)` exists for, and there is no error channel to put it on
because this is a decode rather than an operation that can fail.

**`tests/svc-uart-ns16550` / `svc-uart-pl011` — the driver child, per chip.**
The M3 `child-echo-*` device half UNCHANGED — same register block, same
`listen`, same drain, same ack-is-a-release ordering, same window arriving by
`give` and mapped by the driver into itself — plus a server face: the outlet
GIVEN to its Waiter (consuming, persistent), its Interrupt attached beside it on
the SAME Waiter, a 16-byte input ring, and a pending set of up to four held
obligations ordered by insertion sequence. There is no `take` in either file.

**`tests/svc-client`** — the asymmetry. One sibling inlet, one masked System,
and a loop of `send(Read)` / `post(Write)` / drop. It prints nothing.

**`tests/uart-service`, `tests/uart-cancel`, `tests/delegate-3p`,
`tests/child-forward`, `tests/child-far`** — three launchers and two more
children.

**`hal/{riscv32,arm64}/user/child2.ld` — NEW, and the brief's "any hal edit the
row census requires" is what authorises them.** Unit 5 is the first case in this
tree with TWO children alive at once, and under the no-translation contract two
child images cannot share a load base. Both scripts are their `child.ld` sibling
with one number changed, and both files' own headers had already predicted this
one ("a second child would take the next 256 KiB, and the config — not this file
— is what would say so"). The runner assigns destination regions BY INDEX, so a
package's linker script is now a contract with the order its case lists it in,
and that is written at both ends. One collision came with it and is REFUSED
rather than commented: the RAM pool's base is the second child's region, so
`_region_rows` raises on a case asking for two children and a pool.

**`tools/sos_runner.py`** — five case entries, eight package constants, the
two-children-and-a-pool refusal, and three comment updates. **NOT ONE
NON-COMMENT LINE WAS DELETED** (checked mechanically), so no existing case's
assertion set moved.

**Docs** — spec.md: §2.1 gained a unit-5 block (what a blocking read, a
cancellation, a TELL and multiple clients each did NOT need), the delegation
bullet gained the three-process spelling, §9 gained "a driver is a service" as
its next chapter, §2's `Pipe` row records that unit 5 added nothing to it, §2's
`Process` row records the bump and its census, and §11's "what remains is unit
5" became what unit 5 built. `tests/pipe-delegate-msg` and `tests/pipe-pingpong`
lost their `MAX_PROCESSES`-is-two clauses.

## Root's row census at peak wiring — the brief's forcing question

**THE BRIEF WATCHED THE WRONG TABLE, AND THE RIGHT ANSWER IS ROOMIER.** It names
"root's 8 rows during a two-child launch (its segments + stack + two staged
child images + pool splits)" as the squeeze. Root spends NO row on a staged
child image and none on a pool: `process_create` reads a child's image with
KERNEL privilege (`kcore.dispatch`'s `process_create` takes two Memory slots and
copies through the loader), and none of this unit's launchers maps anything at
all. A launcher hands out CAPABILITIES.

**PROTECTION ROWS (the grant record), counted at every launcher's peak:**

| | rows used | budget | free |
|---|---|---|---|
| root, riscv32 | 3 | `hal.GRANT_ROW_BUDGET` = 8 | 5 |
| root, arm64 | 3 | 9 | 6 |

The three are `IMAGE_GRANT_ROWS`: two loadable segments (the transcript's
`segments=2`) plus the kernel's stack grant at the region's top, recorded by
`place_image` at boot. That number does not move with the number of children,
with the number of connections, or with anything else a launcher does — root
installs zero rows at run time in all three new cases. **NO STOP-AND-PARK WAS
NEEDED, and the reason is structural rather than lucky.**

**HANDLE ROWS ARE THE REAL SQUEEZE, and this is where the census earns its
keep.** `MAX_HANDLES` is 16. Root's table through `uart-service`'s wiring,
counted call by call:

```
 3   boot: System, Process, Thread
+5   boot regions: 2 image blobs, 2 destinations, the device window   =  8
+1   process_self()                                                   =  9
+2   process_create x2                                                = 11   <- PEAK
-4   the four spent Memory wrappers released                          =  7
+2   pipe_create                                                      =  9
+1   inlet.mint (the client's sibling)                                = 10
+1   system.mint (driver)                                             = 11
-3   give system / iomemory / outlet                                  =  8
+1   post -> the claim                                                =  9
+1   system.mint (client)                                             = 10
-2   give inlet / system                                              =  8
-1   resolve consumes the claim                                       =  7
+1   waiter_create                                                    =  8
```

Peak **11 of 16**. Counted WITHOUT the four releases the peak is 15 — one row of
headroom — so the release is not load-bearing today and is one create away from
being so. It is written out with its reasoning at the site, because a launcher
that wires two children is the first program in this tree for which handle-table
hygiene is a real consideration rather than tidiness. `delegate-3p` peaks at 11
of 16 by the same count (four boot rows instead of five, two connections instead
of one).

**AND THE CHILDREN ARE NOWHERE NEAR THEIR OWN WALLS.** The driver's peak is 6
plus up to 4 held obligations = 10 against `DEFAULT_QUOTA_HANDLES` = 12; its
teardown line reads `handles=2`, because every wrapper it held went out of scope
before `exit` — RAII, over kernel handles, at the end of a real program. The
client's teardown reads `handles=3` and that count IS the demonstration.

## The phase protocol: why the order is not the scheduler's

**ROOT'S READ IS FIRST IN THE RING BY CONSTRUCTION.** Root posts it BEFORE
`client.start()`. A post is a completed syscall; at the moment it returns, the
process that will make the second request has not executed an instruction. So
the ring's FIFO order is decided by a fact about program order, not by a race:

1. root's `Read` is staged (syscall complete);
2. the client process starts and issues its own `Read`, which is necessarily
   behind root's in the same connection's ring;
3. the driver's consuming attach delivers in ring order, so its pending set
   holds root's obligation at sequence 0 and the client's at 1;
4. `answer_held` serves OLDEST FIRST, so the first input byte answers root's
   claim and the second answers the client's.

Nothing in that chain asks which process the scheduler runs. Note step 3 is what
made the pending set order by an explicit `seq` rather than by slot position: a
slot frees in the MIDDLE when a client abandons, so "the lowest live index" and
"the oldest request" are different questions.

**THE ROUTING PROOF IS THE CONSOLE PLUS ONE NUMBER.** `Zq7#` appears verbatim
and root prints `byte 90` (0x5A, `Z`). Those two facts together account for all
four input bytes exactly once and in order: root's claim got `Z` and only `Z`,
and the client's three claims got `q`, `7`, `#` in that order. A driver that
answered the wrong claim, answered one twice, or served its pending set in the
wrong order could not produce both rows. **TWO OUTSTANDING READS, TWO PROCESSES,
TWO TABLES, AND NO CORRELATION ON THE WIRE** — the obligation IS the routing.

**ONE HONEST DEPENDENCY, STATED.** The READ order above is protocol-forced. The
ECHO order additionally rests on the harness's `SERIAL_TYPE_DELAY_S` = 0.15 s
inter-byte gap: root's write is triggered by byte 0 and the client's first by
byte 1, so they cannot be reordered unless two bytes arrived in one interrupt,
which 150 ms rules out. That is the same constant every echo case in this tree
has depended on since M2, and it is why the client SYNCS before exiting (below).

**PHASE B AND C NEED NO ARGUMENT AT ALL**: root parks on the client's DEATH
before doing anything of its own, and its closing round ends with a `Sync` whose
answer proves the driver served both preceding writes — a connection's ring is
FIFO, so an answered `Sync` is an ordering barrier the protocol already had.

## Trap accounting, measured

**THE DRIVER, over the marked window (`Mark`, two `Write`s, `Sync`):**

```
SOS uartsvc drv: after the mark answered=2 discarded=2 traps=7
SOS uartsvc drv: one trap per answered message, two per discarded
```

identical in `uart-service` and in `uart-cancel`, from the same driver program
against two different launchers — which is what makes it a fact about the LOOP
rather than about a case. `uart-cancel` is the exact one: it feeds no stdin, so
the device never raises its line and the whole run contains no interrupt.

**`pipe-cq`'s ONE TRAP PER MESSAGE HOLDS, AND THE MEASUREMENT REFINED THE
HEADLINE.** `reply_wait` discharges an obligation and parks for the next in one
op, so every message the driver ANSWERS costs exactly one trap. A message it
DISCARDS costs two, and the second is not overhead:

- a `Write` is post-then-drop at the client, so nothing is owed back and there
  is nothing to reply to;
- the obligation the delivery minted must still be RELEASED, and a release is a
  syscall — because a one-way message carries a real capability rather than a
  datagram;
- there is no cheaper spelling: `reply_wait` on an abandoned claim answers
  `Err(Reply(...))` WITHOUT parking, so it would cost the discharge and then the
  `wait` anyway.

`traps == answered + 2 * discarded + 1`, and the `+ 1` is the closing `stats()`
read charged at trap entry.

**THE CLIENT'S SIDE IS SYMMETRIC AND IS ALSO ASSERTED**: `client traps=13`, read
by root through the Process handle that outlives the client (design 16's
arrangement). One `process_self`, one `boot_handle_next`, three `send(Read)` at
ONE TRAP EACH — each of which PARKED until a device interrupt in another process
made an answer possible — three `post(Write)` plus three claim releases, one
`send(Sync)`, one `exit`.

**SO A TELL IS NOT CHEAPER THAN A CALL ON THIS ABI**, at either end: two traps
against one, on both sides. What a TELL buys is LATENCY — it does not park — and
that is the honest trade to record beside §2.1's TELL idiom. A protocol whose
writes must be cheap should batch them into one message, which `uartproto`'s
`text()` already does for a whole line.

## Deviations from the brief, argued

**NONE FROM THE RULED SURFACE.** Three notes where the built thing is more than
the brief spelled out:

1. **The protocol has FOUR ops, not two.** `Read` and `Write` are the brief's;
   `Sync` and `Mark` are additions, and both are userspace-only like the rest of
   the module. `Sync` is a genuine protocol need rather than instrumentation: a
   client whose writes are TELLs has no way to learn when its bytes reached the
   device, and the ring's FIFO order makes an answered `Sync` exactly that
   barrier — root needs it before printing its own lines, and the client needs
   it before EXITING (see finding 1). `Mark` is instrumentation and says so at
   its declaration: it tells the driver to take a trap reading so the serve
   loop's cost can be measured over a window the case controls. It carries no
   authority and reads no device. Neither is kernel surface; both are one byte
   in a test protocol.
2. **The trap headline is "one trap per ANSWERED message, two per discarded"**
   rather than the brief's flat "one trap per message". That is a measurement
   result, not a design choice — see the accounting above. The brief's number is
   correct for the workload `pipe-cq` measured, and the refinement is what a
   real driver, serving TELLs as well as calls, actually costs.
3. **`uart-cancel` is TWO cases and `uart-service` is TWO cases**, one per chip,
   because a driver names its device — the M3 `child-echo-*` split unchanged.
   The launchers, the client and the protocol are single arch-free packages.

## Transcript accounting

Baseline captured at `d88def5` before any edit; gated on this branch. The
harness prints console transcripts only on failure, so the accounting is over
the two things a passing gate surfaces — the case report and the image-size
rows — plus the `expect_out` assertion sets every console row is matched
against.

| bucket | riscv32 | arm64 |
|---|---|---|
| byte-identical image rows | 109 | 111 |
| address-only | 0 | 0 |
| authorized-with-cause image rows | **2** | 0 |
| documented-nondeterministic | 0 moved | 0 moved |
| new image rows | 7 | 7 |
| case rows, byte-identical | 113 | 113 |
| new case rows | 3 | 3 |

**THE TWO AUTHORIZED ROWS ARE NAMED, AND THEY ARE THE ONLY THING THE BUMP MOVED
IN AN EXISTING TRANSCRIPT:**

- `tests/process-reclaim` (riscv32) 30,872 -> 31,296 B (+424)
- `tests/death-late-attach` (riscv32) 40,640 -> 41,048 B (+408)

**CAUSE: `MAX_PROCESSES` = 3, and each case had to arrange a FULL TABLE to keep
its own proof load-bearing.** Both ask whether a process slot is AVAILABLE —
`refused_before=1` and `held=1` are the halves that matter — and that question
only has an answer when the table has nothing else to give. Each now creates one
unstarted FILLER process to spend the slot the bump added, which is the +400-odd
bytes. **THEIR ASSERTED ROWS ARE BYTE-IDENTICAL** (`refused_before=1
reclaimed_after=1`, `held=1 freed=1`), which is what says the proofs survived
the bump rather than being re-aimed by it, and both are now written against "the
table is full" rather than against a number, so the next bump is one more
filler. Their arm64 twins did not move at all: arm64 images are page-rounded and
400 bytes vanished into the rounding, which is design 17's own finding about
this transcript.

**NOTHING ELSE MOVED.** 109 + 111 image rows byte-identical, the shared case
rows in an IDENTICAL ORDER on both arches (checked mechanically, not by eye), no
case row removed, no case row carrying a mark other than the pass mark. The
three cases carrying documented-nondeterministic rows (`thread_preempt`,
`timer_interval`, `process_stats`) assert nothing this unit touches and their
image rows are byte-identical in both columns.

**MECHANICAL**: every `[n/113]` report line became `[n/116]` (226 lines) and the
summary moved `226 passed` -> `232 passed`, which is the six new case rows
arriving.

**THE KERNEL IMAGE IS NOT A PRINTED ROW**, so the `.bss` the bump added —
`HANDLES` 32 -> 48 entries, `BOOT_HANDLES` with it, the quota pair, one
`ProcessSlot` — is invisible here and costs no image bytes by design 149's
zero-static rule. The one place a kernel-size change has historically been
observable is `arm64/trap_fault`'s asserted row, and it did not fire: that case
asserts `ec=` and a non-zero exit, not the trapping PC (design 9's own
accounting records the distinction).

## Findings

1. **A TELL CAN OUTLIVE ITS SENDER'S CONSOLE OUTPUT, AND THAT IS TAKE-AT-POST
   BEING RIGHT RATHER THAN A BUG.** The client's first spelling exited straight
   after its last `Write`, and the console read `Zq7`, then two kernel teardown
   rows, then `#`. The byte was never lost — the message was staged and the
   driver wrote it — but it was written AFTER the kernel's own exit line,
   because a TELL returns as soon as the message is staged. This is exactly the
   guarantee unit 4's re-rule bought (a staged message outlives its sender), met
   from the other side: a client that wants its output ORDERED against its own
   death has to say so, and the protocol already had the word. One `Sync` before
   `exit` fixes it in one trap. Recorded at the client's own site, because
   "flush before you exit" is a thing every future client of a byte-stream
   service will need and nothing about it is obvious from the ratified surface.
2. **A `Waiter` SERVING A DEVICE AND A CONNECTION AT ONCE NEEDED NOTHING ADDED,
   AND THIS IS THE FIRST PROGRAM IN THE TREE THAT DOES IT.** The driver attaches
   its Interrupt and gives it its `PipeOutlet`, and a device event and a client
   request arrive through one `wait` distinguished by the record's tag. §2.2 was
   ratified for exactly this and every case before now attached one KIND of
   thing. The one thing it cost was a dispatch shape: the loop decides what a
   delivery IS by borrowing the record (`kind_of`), and only the arm that will
   MOVE payloads out opens it — SL-17's discipline, and the borrowing look is
   what makes a four-way dispatch writable at all.
3. **THE PENDING SET IS AN ARRAY OF `PipeRequest?` AND IT WORKS**, which is
   worth recording because the tree had no precedent for a mutable one:
   `PipeMsg` holds `[PipeHandle?; 4]` but nothing ever ASSIGNS into such a slot,
   and `PipeHandles` deliberately keeps words rather than wrappers because a
   match-arm binding cannot be disarmed. Probed before it was written (drop
   counter, hosted): assignment into an optional array slot, `is_none`/`is_some`
   on it, and `Optional.take()` out of it all behave, at a NoCopy payload, with
   exactly one drop per value. The attach-then-move ORDER matters and is written
   at the site — `waiter.add(request: &obligation, ...)` while the wrapper is
   still a local, THEN `held.put(...)` — because an optional payload cannot be
   borrowed out of the slot afterwards.
4. **NO NEW SAWLANG DEFICIENCY.** Two walls, both already filed. SL-13 (a
   `&var [T; N]` parameter's elements are not assignable) reached a package's
   PUBLIC SURFACE for the first time — `uartproto`'s message builder is a
   one-field struct because of it, and every consumer of the protocol now names
   that type; a second site is recorded at the entry. SL-8/DF-172d bit twice,
   and the second face is new enough to be worth the note it got: a wrapped
   ASSIGNMENT (as opposed to a wrapped condition) fails at the line BELOW with
   `Unexpected token: NEWLINE`, and it bites hardest where indentation is
   deepest — which is exactly where a line is most likely to need wrapping.
