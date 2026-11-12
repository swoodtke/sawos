/* Arena override, riscv32: a package that chooses its own arena.
 *
 * Assigns `ARENA_SIZE` and INCLUDEs the script every other root package names;
 * the shared script `PROVIDE`s the default, so this assignment wins for this
 * image only. The INCLUDE path is spelled as the manifest would spell it:
 * `ld.lld` runs with the package directory as its working directory.
 *
 * The size is load-bearing for `src/main.saw`: it must hold every block both
 * workers allocate with room to spare, since nothing is reclaimed.
 */
ARENA_SIZE = 96K;

INCLUDE ../../hal/riscv32/user/root.ld
