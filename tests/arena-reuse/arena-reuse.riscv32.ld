/* An 8 KiB arena, so the program's churn passes its size many times over.
 * `tests/arena-small/arena-small.riscv32.ld` explains the override.
 */
ARENA_SIZE = 8K;

INCLUDE ../../hal/riscv32/user/user.ld
