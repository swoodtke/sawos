/* Arena override, arm64. Same shape as the riscv32 sibling, which carries the
 * explanation; a package names one `linker-script` per target triple.
 */
ARENA_SIZE = 96K;

INCLUDE ../../hal/arm64/user/user.ld
