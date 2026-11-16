/* Per-package arena override, riscv32: a small arena, then the board's
 * script. `tests/arena-small` explains the mechanism. */
ARENA_SIZE = 4K;

INCLUDE ../../hal/riscv32/user/user.ld
