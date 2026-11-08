/* Per-package arena override, arm64. Same two lines as the riscv32 sibling,
 * which carries the explanation; a package names one `linker-script` per
 * target triple, so the override is written once per target.
 */
ARENA_SIZE = 8K;

INCLUDE ../../hal/arm64/user/user.ld
