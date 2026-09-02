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
