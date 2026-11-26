/* Arena override, arm64. Same shape as the riscv32 sibling, which carries the
 * explanation; a package names one `linker-script` per target triple. A block
 * is twice as large here, so the arena is larger by as much and more.
 */
ARENA_SIZE = 112K;

INCLUDE ../../hal/arm64/user/user.ld
