/* THE PER-PACKAGE ARENA OVERRIDE (sawos design 38) — arm64, Profile B.
 *
 * The riscv32 sibling beside this file carries the argument; this is the same
 * two lines against the other profile's shared script. That there IS a second
 * file is the manifest's doing, not the mechanism's: a package names one
 * `linker-script` per target triple, so an override that applies to both
 * targets is written once per target — exactly as `root.ld` / `user.ld` are.
 */
ARENA_SIZE = 8K;

INCLUDE ../../hal/arm64/user/user.ld
