/* THE C CHILD'S SCRIPT (sawos design 39) — riscv32, Profile A.
 *
 * **DESIGN 38's OVERRIDE, USED FOR THE ONE CASE IT WAS NOT WRITTEN FOR: ZERO.**
 * `ARENA_SIZE` is `sosrt`'s bump-allocator region, and every user script
 * `PROVIDE`s 64 KiB of it because every freestanding SAW image needs one — the
 * compiler's own panic path can allocate. This image has no Saw in it. It links
 * no `sosrt`, references neither `_arena_start` nor `_arena_end`, and would
 * carry 64 KiB of `.bss` it can never name.
 *
 * So it sets the knob to 0 and INCLUDEs `child.ld` unchanged, which is design
 * 38's mechanism working exactly as `tests/arena-small/` documents it — and the
 * answer to design 39's finding 3, which asked whether the arena convention
 * fits a non-Saw image. It fits BECAUSE it is a knob: the arena is not a
 * requirement of the format, of the loader or of the region layout, it is one
 * package's storage, and a package that does not want one says so in two lines.
 *
 * The symbols still get DEFINED (the shared script assigns them either side of
 * a zero-length gap) and nothing references them, so `--gc-sections` and the
 * sosimg emitter never see them. The measurable effect is on the image's
 * `mem_len`: this child's region costs its segments and its 16 KiB stack, and
 * nothing else.
 *
 * INDEX COUPLING, unchanged from `child.ld`'s own header: this profile places
 * by identity, so an image linked with `child.ld` runs at `0x8024_0000` and the
 * case must list this package FIRST in its `children`.
 */

ARENA_SIZE = 0;

INCLUDE ../../hal/riscv32/user/child.ld
