/* Per-package arena override, xtensa: a small arena, then the board's
 * script. `tests/arena-small` explains the mechanism. */
ARENA_SIZE = 4K;

INCLUDE ../../hal/xtensa-esp32s3/user/user.ld
