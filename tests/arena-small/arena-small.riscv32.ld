/* Per-package arena override, riscv32.
 *
 * A package that wants an arena other than the shared default names a script
 * of its own in its manifest, assigns `ARENA_SIZE`, and INCLUDEs the script it
 * would otherwise have named. The shared script `PROVIDE`s the default, and
 * `PROVIDE` defines a symbol only when nothing else has, so the assignment
 * below wins and every other package is untouched. Nothing in `hal/` knows
 * this package exists.
 *
 * The INCLUDE path is spelled as the manifest would spell it: Blade runs
 * `ld.lld` with the package directory as its working directory and passes
 * `-T <the manifest's linker-script string>`, and `ld.lld` resolves an INCLUDE
 * relative to that same directory.
 *
 * 8 KiB is load-bearing for `tests/arena-small/src/main.saw`: large enough
 * that a 4 KiB request, which the shared default cannot serve, can be (proving
 * the override reached the image), small enough that the 16 KiB request after
 * it cannot.
 */
ARENA_SIZE = 8K;

INCLUDE ../../hal/riscv32/user/user.ld
