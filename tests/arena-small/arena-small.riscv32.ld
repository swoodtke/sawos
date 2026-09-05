/* THE PER-PACKAGE ARENA OVERRIDE (sawos design 38) — riscv32, Profile A.
 *
 * **THIS FILE IS THE WHOLE MECHANISM, AND IT IS TWO LINES.** A package that
 * wants an arena other than the shared 64 KiB names a script of its own in its
 * manifest, assigns `ARENA_SIZE`, and INCLUDEs the script it would otherwise
 * have named. The shared script `PROVIDE`s the default, and `PROVIDE` defines a
 * symbol only when nothing else has — so the assignment below wins and every
 * other package is untouched. No build flag, no conditional compilation (this
 * toolchain has none), and nothing in `hal/` knows this package exists.
 *
 * THE INCLUDE PATH IS SPELLED EXACTLY AS THE MANIFEST WOULD HAVE SPELLED IT.
 * Blade runs `ld.lld` with the PACKAGE DIRECTORY as its working directory and
 * passes `-T <the manifest's linker-script string>`, and `ld.lld` resolves an
 * INCLUDE relative to that same working directory — so a script fragment and a
 * manifest entry name a sibling file the same way, and there is no second path
 * convention to learn.
 *
 * 8 KiB, and both halves of the number are load-bearing for the case in
 * `tests/arena-small/src/main.saw`: it is small enough that a 16 KiB request
 * cannot be served (which is what proves the override reached the image) and
 * large enough that the 2 KiB request before it is (which is what proves the
 * region is real rather than merely absent).
 */
ARENA_SIZE = 8K;

INCLUDE ../../hal/riscv32/user/root.ld
