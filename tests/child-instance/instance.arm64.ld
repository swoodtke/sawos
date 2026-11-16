/* Per-package arena override, arm64: a small arena, then the board's
 * script. `tests/arena-small` explains the mechanism. */
ARENA_SIZE = 4K;

INCLUDE ../../hal/arm64/user/user.ld
