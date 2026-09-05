# SawOS design 38 — M5 arena unit: per-package arena + board-scaled kernel stack

**Status: BUILT, Sep 5 2026 (As-built at the end) — dispatched under
`designs/025` (seed 4 IN; "slots at convenience").** Gate 377/377 across
three profiles (was 374); with the `[i/N]` total and the image-size column
normalised out the transcript diff is SIX HUNKS, every one an addition and
every one the new case's own row — not one pre-existing case row moved on any
profile. NO TOOLCHAIN CHANGE was needed (the brief's STOP condition did not
fire): `PROVIDE` plus a package-local `INCLUDE` is the whole override. No SL
entry owed (highest remains SL-24). **Two things the lead must see: the
mechanism costs riscv32 images 56–128 bytes of image each (measured, explained,
§4) and the ESP32-C3 board smoke is RED AT THIS UNIT'S BASE and it is design
37's, bisected (§7).**

The measured small-board RAM levers:
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

---

# As-built (agent, Sep 5 2026)

Branch `m5-arena-sizing`, on `588ab67` (the sawlang 0.6.0 pin bump).
Baseline reproduced at that commit before any edit: **374/374
(126 + 126 + 122)**, 793 transcript lines.

## 1. The shape as landed

Three files carry the mechanism and none of them is new machinery.

**The linker scripts** (seven user + three kernel) `PROVIDE` a knob and
reserve a region inside `.bss`:

```
PROVIDE(ARENA_SIZE = 64K);
...
    .bss (NOLOAD) : {
        *(.sbss .sbss.*) *(.bss .bss.*) *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > ROOT
```

**`rt/common_c/support.c`** reads the pair back, because Saw cannot name a
linker symbol — the same wall `sink.c` meets for `.payload`, answered the same
way and with the same `extern unsigned char sym[]` idiom:

```c
extern unsigned char _arena_start[];
extern unsigned char _arena_end[];
usize sos_arena_base(void) { return (usize)_arena_start; }
usize sos_arena_size(void) { return (usize)(_arena_end - _arena_start); }
```

**`rt/common/src/lib.saw`** loses `type ArenaRegion`, `unsafe static var ARENA`
and the two private `arena_base()` / `arena_bytes()` helpers, declares the two
accessors in a second `extern "C"` block, and calls them from `rt_alloc`.
`ARENA_NEXT`, the alignment-of-the-absolute-address rule and the
subtraction-form bound check are untouched, line for line.

THE SYMBOL CONTRACT IS A PAIR OF ADDRESSES, not a base and a length. The brief
proposed `_arena_base` / `_arena_size`; `_arena_start` / `_arena_end` is what
landed, for two reasons — it is the spelling `_payload_start` / `_payload_end`,
`_region_table_start` / `_region_table_end` and `_bss_start` / `_bss_end`
already use in these same scripts, and it keeps C out of the
absolute-symbol-as-value idiom (`extern char SIZE[]`, where the ADDRESS is the
number) for a value the linker can give as a real subtraction. `ARENA_SIZE`
remains as the knob a package assigns, and the scripts' own naming rule is
arm64's existing `LINMAP_OFFSET` precedent: CAPS is a number you may change, a
leading underscore is an address the program uses.

THE C FILE'S HEADER GAINED A THIRD REASON. It said "every line in this file is
C for one of TWO reasons, and both are PERMANENT"; the third is "a linker
symbol has no Saw spelling", which is as permanent as the other two and is the
same reason `sink.c` exists. It is deliberately NOT called a seam: a seam has
one answer per side, and this has one answer for both sides, which is why it
belongs in the file both sides already link rather than in either HAL.

## 2. The override — the brief's STOP condition did not fire

The brief authorised a stop if every candidate override needed a toolchain
change. None does. The mechanism is `PROVIDE` plus `INCLUDE`, and the whole of
a package's override is `tests/arena-small/arena-small.riscv32.ld`:

```
ARENA_SIZE = 8K;

INCLUDE ../../hal/riscv32/user/root.ld
```

Three facts make that work, all verified rather than assumed (a standalone
`ld.lld` experiment first, then the real build):

1. **`PROVIDE` defines a symbol only when nothing else has**, script
   assignments included — so the assignment above wins and the shared script's
   default fires for every package that says nothing. Confirmed at the symbol
   level: `ARENA_SIZE` reads `0x10000` in every other image and `0x2000` in
   this one, on both profiles.
2. **`ld.lld` resolves `INCLUDE` against its working directory**, and Blade
   runs the linker with the PACKAGE DIRECTORY as that working directory
   (`_build_root_image` passes `cwd=pkg_dir`) — which is the same directory the
   manifest's own `linker-script` path is relative to. So the fragment spells
   the shared script exactly as the manifest would have, and there is no second
   path convention to learn.
3. **A package names one `linker-script` per target triple**, so an override
   that applies to both targets is two files. That is the manifest's shape, not
   the mechanism's — `root.ld` / `user.ld` are already two.

No build flag, no `--defsym`, no conditional compilation, and nothing in `hal/`
knows the package exists.

## 3. The kernel stack — and the brief's premise was half wrong

The brief says the 64 KiB boot stack is "a linker-script constant". IT WAS
NOT: it was a `.skip 0x10000` inside a `.bss.stack` section in each board's
`boot.S`. That makes the goal MORE worth doing, not less — a board variant
inherits its board's `boot.S` wholesale (design 35's flat profile shares
`hal/riscv32/kernel/boot.S` outright), so the one number a board might want to
change was living in the one file the variant does not get to edit.

It is `KERNEL_STACK_SIZE = 64K;` in each of the three kernel scripts now, with
the reservation beside the arena's inside `.bss`, and the three `boot.S` files
carry a comment where the `.skip` was. A plain assignment rather than a
`PROVIDE`, deliberately: nothing overrides a kernel's stack from outside, so
the knob is the board's own number and reads as one.

`_stack_top` and `_stack_bottom` are unchanged as symbols; `boot.S` still
`la sp, _stack_top` / `ldr x0, =_stack_top` and `trap.S` still reloads it. Its
header's "the board's kernel stack, from its `.bss.stack`" is amended, since
that contract line is the one place the arrangement was written down.

**THE ONE SAFETY QUESTION, CHECKED RATHER THAN ASSUMED.** The stack used to sit
at the HEAD of `.bss` (it was the first input section); it is at the TAIL now.
That is only safe if nothing is on the stack when the `.bss` zero loop runs —
so both boot paths were re-read: riscv32 sets `sp`, writes two CSRs and then
zeroes, and arm64 sets `sp`, enables FP/SIMD and then zeroes. Neither pushes
anything, and arm64's own comment already said so ("nothing here calls
anything").

## 4. Measured cost — the sizes, which are this unit's deliverable

### The RAM side (what the unit is for)

`.bss` did not grow anywhere. It SHRANK slightly, from the removed static's own
padding:

| image | `.bss` before | after | delta |
|---|---|---|---|
| `sos-root`, riscv32 | 65,568 | 65,556 | −12 |
| `sos-root`, arm64 | 69,632 | 69,632 | 0 |
| kernel `process_isolation`, riscv32 | 148,048 | 148,032 | −16 |
| kernel `process_isolation`, arm64 | 287,312 | 287,312 | 0 |

And the override buys exactly what it should — `tests/arena-small` against a
default package, same profile:

| image | `.bss` at 64 KiB | at 8 KiB | saved |
|---|---|---|---|
| riscv32 | 65,556 | 8,212 | **57,344** |
| arm64 | 69,632 | 12,288 | **57,344** |

57,344 is 56 KiB exactly, which is the arena difference and nothing else. For
the number design 20 finding 4 actually cared about: the C3 child measured
67,600 bytes of region, of which 65,536 was arena — at 8 KiB that child is
**10,256 bytes**, and the 92 KiB child region would hold eight of it.

### The IMAGE side — a finding, brought back rather than normalised

The brief expected NO size-row motion and asked for it to be verified. **Half
of it held and half did not, and the half that did not is the accessor rather
than the arena.** The arena's bytes are `.bss` and stayed out of the image, as
predicted. But `__saw_rt_alloc` used to reach a `static` and a `sizeof`, both
of which the compiler folded into it as immediates; it now makes two calls.
Measured on `sos-root`, riscv32, rebuilt at the base sources for the exact
before:

| | before | after | delta |
|---|---|---|---|
| `__saw_rt_alloc` | 114 B | 138 B | +24 (two calls, and the frame they need) |
| `sos_arena_base` | — | 10 B | +10 |
| `sos_arena_size` | — | 22 B | +22 |
| `.text` | 3,036 | 3,100 | +64 |
| `.data` | 596 | 604 | +8 (one more `.LCPI` constant-pool entry) |
| sosimg | 3,704 | 3,776 | +72 |

Across the whole suite, per profile:

| profile | images | motion |
|---|---|---|
| riscv32 | 133 | ALL moved: +56 (13), +72 (13), +80 (14), +96 (55), +112 (34), +128 (4) |
| riscv32-flat | 129 | ALL moved: +56 (12), +72 (12), +80 (14), +96 (54), +112 (34), +128 (3) |
| arm64 | 134 | **133 BYTE-IDENTICAL**; one image +4096 |

The riscv32 spread is one number plus rounding: ~64 bytes of `.text` and 8 of
`.data`, then the sosimg's 16-byte re-round of where `.data` starts. arm64
pays nothing at all because `user.ld` pads `.text` to a page — except
`pipe-pingpong`, whose `.text` was inside 60 bytes of a page boundary and
crossed it (45,056 → 49,152). That is the whole of the arm64 motion, and it is
one image.

**This is design 32's +40-bytes-per-riscv32-image finding again, from a
different cause**: there it was a new floor `@export` landing in `llvm.used`,
here it is an inlined constant becoming a call. Both say the same thing about
this profile — riscv32 images are byte-tight, and anything added below the
whole tree shows up in every one of them.

The alternative that would have avoided it is inlining `sos_arena_size` into
`rt_alloc`, which needs either LTO or a Saw spelling for a linker symbol.
Neither exists, and inventing one is the toolchain change the brief forbade.

The kernel ELF pays the same way and it is invisible to the gate (the
transcript's image column is the userspace sosimg): `.text` +52 on riscv32,
+40 on arm64, `.rodata` and `.data` unmoved on both.

## 5. Gate evidence

Baseline at `588ab67` reproduced exactly before any edit: **374/374
(126 + 126 + 122)**. After: **377/377 (127 + 127 + 123)**, `arena_small`
appended at the END of the case list per the file's own ordinal-stability
convention.

The raw diff is six hunks, and three of them are the whole case list on each
profile — because `[i/N]` carries the TOTAL, so adding a case rewrites every
row's denominator (design 35's finding (b), unchanged). **With the `/N` and the
image-size column normalised out, the diff is six hunks, every one an addition,
and every one is the new case's own row or its image line:**

```
142a143 >   tests/arena-small/...riscv32.../arena-small.sosimg (SIZE)
268a270 > [127] ✓ arena_small
404a407 >   tests/arena-small/...aarch64.../arena-small.sosimg (SIZE)
530a534 > [127] ✓ arena_small
661a666 >   tests/arena-small/...riscv32.../arena-small.sosimg (SIZE)
789a795 > [123] ✓ arena_small
```

Not one pre-existing case row moved on any profile. The three timing-dependent
cases CLAUDE.md names did not move on this run either.

## 6. The proof, and why both brackets are load-bearing

`tests/arena-small` is a root package with `ARENA_SIZE = 8K` that makes two
requests:

```
SOS arenasmall: 2 KiB ok          <- 512 UInt32 reserved, written, read back
SOS arenasmall: asking 16 KiB     <- printed BEFORE the ask, which does not return
sos: out of arena memory          <- sosrt's own line
```

and the case asserts those three plus `expect_status: 65`
(`AbortCode.NoMemory`, added to the runner as `EXIT_ARENA_EXHAUSTED` beside
`EXIT_PROCESS_FAULT`). There is no op that reports an arena size and there
should not be — a program that could ask is a program that could branch on it
— so the pair of brackets IS the measurement: served at 2 KiB and refused at
16 KiB pins the region to `[2 KiB, 16 KiB)`, which the 64 KiB default cannot
produce. Both numbers are profile-independent (`UInt32`, not `Int`), which is
what lets one program make the same claim on all three.

**BOTH BRACKETS WERE PROBE-VERIFIED, not argued.** With the manifest pointed
back at the shared `root.ld` the case FAILS, on exactly the line it should:

```
[1/1] ✗ arena_small  (missing expected output 'sos: out of arena memory' ...
        SOS arenasmall: 2 KiB ok
        SOS arenasmall: asking 16 KiB
        SOS arenasmall: UNREACHABLE, got 0 words
```

So an image that misses the override fails loudly rather than passing by
omission, which is the property a mechanism test has to have.

It runs on all three profiles: a link address and a bump cursor are the same on
a flat platform as on an isolated one, so nothing here is tiered.

INCIDENTALLY THE FIRST `Vector` IN THE SOS TREE. Nothing in `kernel/`, `root/`
or `tests/` had ever used one — the kernel builds under `--no-hidden-alloc` and
every other test package walks string literals. It works freestanding with no
ceremony, which is worth knowing: std's allocating surface reaches
`__saw_rt_alloc` and therefore this arena, and `Vector(capacity:)`'s
`Result<_, AllocError>` channel is dead on this runtime because the allocator
ABORTS instead of answering (the case's `catch` arms say so, and would report
it if that ever changed).

## 7. Findings

1. **THE ESP32-C3 BOARD SMOKE IS RED AT THIS UNIT'S BASE, AND IT IS DESIGN
   37'S.** `make sos-smoke-esp32c3` fails 0/3 at the LINK —
   `section '.bss' will not fit in region 'SRAM': overflowed by 10,416 bytes` —
   on `588ab67`, before any edit of this unit's. Bisected: **3/3 at `a64d359`,
   0/3 at `9026bf7` (design 37, the stats region) and at every commit after
   it**, including the pin bump, which is therefore not the cause. Design 37's
   As-built records the C3 as "checked to build clean"; that claim does not
   hold. The arithmetic fits the story: its region is 12,288 B of `.bss` on this
   board (sized by `PROT_DOMAIN_SLOTS`, which riscv32 publishes as 256) against
   under 2 KiB of slack.
   **NOT FIXED HERE, on the brief's own instruction** ("shrinking any board's
   defaults" and "the C3 fit re-measure" are out of scope, and retuning a board
   is that board's unit) — but the fit was PROBED so the next agent starts from
   a measurement: `ARENA_SIZE = 16K` in `esp32c3.ld` ALONE frees 48 KiB and
   takes the smoke back to **3/3 with 38,760 bytes of slack**. That is the
   cheapest of the three levers and the best argued — the kernel allocates
   nothing on its steady path, every kernel source builds under
   `--no-hidden-alloc`, and the arena exists there only so the compiler's panic
   path is backed. The other two are `KERNEL_STACK_SIZE` (this unit's, 48 KiB
   at 16K) and `PROT_DOMAIN_SLOTS` (design 37's, a board number now sizing two
   things). Recorded in `esp32c3.ld`'s header and in the board's `ABI.md`,
   whose SRAM-budget numbers were stale in three places and are corrected and
   dated (the kernel wants 182,424 B of the 172,032 it is granted).
   **This unit's board half is therefore load-bearing on the one board it was
   built for, and that is now measured rather than claimed.**

2. **The image-size motion of §4** — riscv32 pays 56–128 bytes per image,
   arm64 pays one page in one image. Brought back rather than normalised, as
   the brief asked. The arena's `.bss` bytes stayed out of the image exactly as
   predicted; what moved is the ACCESSOR, and the cause is an inlined constant
   becoming a cross-TU call.

3. **The brief's "a linker-script constant" premise about the kernel stack was
   wrong** (§3) — it was a `.skip` in each board's `boot.S`, which makes the
   knob worth more, not less, because a board VARIANT shares its board's
   `boot.S` and could never have overridden it.

4. **`spec.md`'s design-172 retrospective is now stale in one sentence**: it
   says `rt/common_c/support.c` is "`mem*`, the atomic libcalls and … the
   64-bit division pair — reason 2, and reason 2 only". There is a reason 3
   now. NOT edited — the brief puts spec prose in unit 8, which already holds
   design 36's stale capacity prose; filed there.

5. **`.bss` shrank by 12–16 bytes on riscv32** and did not move on arm64. Worth
   one line only because it is the direction nobody expects: the removed
   `static` was carrying its own alignment slack and the linker region does
   not.

6. **No SL entry owed.** Nothing about the language bit: the second
   `extern "C"` block, the `try … catch` guard shape at both statement and
   binding position, and `Vector` in a freestanding image all worked first
   time. Highest remains SL-24.

## 8. What the lead should see before merging

- **The C3 smoke was already broken and this unit did not break it** (finding
  1). The one-line fix now exists and was probed; whether to take it here or in
  the C3's own unit is the lead's call, and the brief said the latter.
- **Every riscv32 image in the transcript changes size** (finding 2). It is
  uniform in cause and 56–128 bytes in effect; arm64 is byte-identical but for
  one image. If that is not acceptable motion, the alternative is not a smaller
  patch — it is a toolchain feature (LTO, or a Saw spelling for a linker
  symbol), which is exactly what the brief forbade inventing.
- **The knob is spelled `ARENA_SIZE` and lives in ten linker scripts**, which
  is nine more copies than a shared fragment would be. An `INCLUDE`d common
  fragment was considered and DECLINED: `INCLUDE` resolves against the LINKER'S
  working directory, which differs between a kernel link and a package link, so
  one fragment could not be named the same way from both — and these scripts
  already duplicate everything else under the tree's sibling-copy rule.
- **Nothing gates the two knobs against each other or against a region's
  length.** An `ARENA_SIZE` a user region cannot hold is a loud
  `ld.lld: section '.bss' will not fit in region` error, which is the same
  guard every other number in these scripts has and is why finding 1 is a link
  failure rather than a boot mystery.
