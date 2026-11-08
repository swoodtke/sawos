/* The C child's linker script, riscv32.
 *
 * `ARENA_SIZE` is `sosrt`'s bump-allocator region, and every user script
 * `PROVIDE`s a default of it because every freestanding Saw image needs one
 * (the compiler's own panic path can allocate). This image has no Saw in it:
 * it links no `sosrt`, references neither `_arena_start` nor `_arena_end`, and
 * would carry a default-sized `.bss` it can never name.
 *
 * So it sets the knob to 0 and INCLUDEs `child.ld` unchanged, as
 * `tests/arena-small/` documents. The arena is one package's storage, not a
 * requirement of the format, the loader or the region layout.
 *
 * The symbols are still defined (the shared script assigns them either side
 * of a zero-length gap) and nothing references them, so `--gc-sections` and
 * the sosimg emitter never see them. The effect shows in the image's
 * `mem_len`: the region costs its segments and its stack, nothing else.
 *
 * Index coupling, as in `child.ld`'s own header: this profile places by
 * identity, so an image linked with `child.ld` runs at `0x8024_0000` and the
 * case must list this package first in its `children`.
 */

ARENA_SIZE = 0;

INCLUDE ../../hal/riscv32/user/child.ld
