# SawOS design 12 — waiter revocation (M4 unit 0)

Status: AUTHORED Aug 31 2026 (lead). M4's first rung, per the ruled
plan of record (designs/010, agenda ruling 1): **when a Waiter's last
reference drops, the threads parked on it WAKE with a distinguishable
terminal status instead of stranding.** The smallest unit of the M4
ladder, sequenced first because it lands the vocabulary everything
later uses — the PEER-GONE DOCTRINE's first instance: nothing parked
can be silently doomed.

## The ruled surface

- **`SosStatus.Revoked`** — a NEW status case, appended. One word for
  this site and every future revocation site (ruling 1's own text).
- **The free arm wakes, it does not count** (answering design 7
  finding 2 as "wake, don't count"): `free_object`'s Waiter arm
  currently detaches the attachment list (the one cascade) and
  deliberately leaves `blocked` unwalked — the recorded
  "legal-but-doomed" stance. It now ALSO walks `blocked` and wakes
  every parked thread with `Revoked`. The husk alternative (a parked
  thread worth 1 to the count) stays REJECTED: it keeps the thread
  alive but makes the drop silent — a hang to infer rather than an
  error to handle.

## D-1: the mechanism (mostly what exists)

- The tracking already exists: `WaiterSlot.blocked` is the parked
  list, and the wake machinery that `notify_ready` uses is the model.
  The revocation wake differs in exactly one way: **NO RECORD IS
  WRITTEN** — there is no key, no tag, no payload, because nothing
  became ready; the parked `wait()` syscall ANSWERS `Revoked` in its
  status register and the caller's buffer is untouched. Write the
  contrast at the site: a delivery copies out then answers Ok; a
  revocation answers Revoked and copies nothing.
- Ordering: the wakes happen inside the syscall that dropped the last
  reference, before the slot is reused (the reused slot starts with
  an empty blocked list exactly as today — that sentence survives;
  what dies is the stranding above it).
- The blocked list may hold threads of ANY process that held a handle
  (Waiter handles transfer); the walk is process-blind, like every
  wake.
- A release that is NOT the last reference (a minted sibling still
  live) frees nothing and wakes nobody — the count is the gate,
  unchanged. Say this at the arm; it is the difference between
  revocation and any-release.
- The deadlock predicate is untouched: the wake is synchronous inside
  a syscall, so no arm of `has_external_wake_source` changes.
- Sysapi: `Waiter.wait` already answers `Result<_, SosStatus>`;
  `Err(Revoked)` flows through with ZERO new surface. The wrapper's
  docstring gains the sentence.

## D-2: what this deliberately does not do

No pipe anything (units 1+); no receipt levels; no revocation of
ATTACHMENTS (removing a waitable from a live Waiter is `remove(key)`,
unchanged); no change to what release does when references remain;
no husk tier.

## The proof (harness)

1. **`waiter-revoked`** — a sibling thread parks in `wait()` on a
   Waiter; the main thread releases the LAST handle; the sibling
   wakes with `Revoked` and prints it. The not-last arm rides in the
   same case: before the final release, release a MINTED sibling
   handle first and show the parked thread did NOT wake (the count
   gates, not the release).
2. **A two-parked arm** if `MAX_THREADS` allows it cheaply: two
   threads parked on one Waiter, one release, BOTH wake with
   `Revoked` — the walk is the whole list, not a pop.

Transcript expectation: existing rows UNMOVED (a new appended status
case changes no existing bytes; the struck strand was reachable by no
shipped case). New case rows only.

## Docs owed

spec §2.2 (the revocation wake; the peer-gone doctrine named as the
contract, with this as its first instance and the reply-pair /
connection-end instances pointed at designs/010), §11 row; the
`refs.saw` Waiter-arm comment REPLACED (the legal-but-doomed
paragraph dies, the reused-slot sentence survives); design 7 As-built
finding 2 gains a RIDER (answered Aug 30/31: wake, don't count —
never rewrite the finding); tracker entry closed in place; As-built
here filled; SL-11+ only if a genuine deficiency is met.

## Out of scope

Everything else in designs/010's ladder; kill; thread waitability.

## As built

(Implementer: the wake path as landed, the no-record contrast as
landed, findings.)
