# SawOS design 38 — M5 arena unit: per-package arena + board-scaled kernel stack

**Status: BRIEF, Sep 4 2026 — dispatches under `designs/025` (seed 4
IN; "slots at convenience").** The measured small-board RAM levers:
the 64 KiB `sosrt` ARENA linked into EVERY process image (design 20
finding 4: the child was 67.6 KB, 65.5 KB of it arena) and the
64 KiB kernel boot stack (a linker-script constant). Arch-free in
intent; touches `rt/`, linker scripts, and one HAL constant per
board.

## The one-sentence goal

A package can choose its arena size (default unchanged: 64 KiB) and
a board can choose its kernel stack, so a C3-class deployment stops
paying ~64 KiB × every process for a buffer most test images never
fill — without conditional compilation, which this toolchain does
not have (unit 4 confirmed).

## The constraint that shapes the design

`ARENA` today is a Saw `static` array in `sosrt` — its size is a
compile-time fact of the SHARED package, so per-package sizing
cannot be a `static` the packages all import (SL-25's lesson at the
other end of the link). The viable shape is LINK-TIME: the arena
becomes a linker-provided region (`_arena_base`/`_arena_size`
symbols; `PROVIDE` defaults in the shared user linker scripts keep
every existing package at 64 KiB unchanged), and `sosrt` initializes
from the symbols. A package that wants smaller says so — the agent
scouts the cleanest per-package override the build already supports
(a manifest-named linker-script fragment beside the shared one, the
same mechanism that already points packages at `user.ld` /
`root.ld`) and records it. NO toolchain change; if every candidate
override needs one, STOP and report — that is an SL entry plus a
redesign, not a workaround to force.

## The kernel stack

The 64 KiB boot stack becomes a per-board named constant in the HAL
linker scripts (virt boards may keep 64 KiB — the point is the KNOB
and its documentation, not shrinking a board with 128 MiB). The C3
board's script is where a smaller number will matter; leave its
value for the board's next real unit, note it there.

## The proof

- **`arena-small`** (new): a test package that overrides its arena
  down (e.g. 8 KiB), allocates within it successfully, and — if
  `sosrt`'s allocator surfaces exhaustion detectably — witnesses
  the honest failure past the limit. It passing on all profiles IS
  the mechanism's proof.
- Every unmodified package must produce BYTE-IDENTICAL images:
  `.bss`-resident arenas do not occupy sosimg bytes, so the
  expectation is NO size-row motion — verify rather than assume,
  and if the static→linker-region move shifts any section layout
  into visible bytes, that is a finding to bring back, not
  normalize.

## Authorized transcript motion

The new case's rows only, all profiles. Everything else
byte-identical against the current baseline at dispatch.

## Out of scope

Shrinking any board's defaults; the C3 fit re-measure (rides the
next esp32c3 unit); `rt/` allocator behavior changes; spec.md prose
(unit 8).

## Recording duties

As-built here: the override mechanism as landed, the symbol
contract, the per-board stack knob's spelling, gate evidence,
findings. SL entries if the language bites (verify the highest
first). Close the tracker entry in place.
