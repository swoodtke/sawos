/* An 8 KiB arena, riscv32: the shared script `PROVIDE`s the default, and
 * this assignment wins over it.
 */
ARENA_SIZE = 8K;

INCLUDE ../../hal/riscv32/user/user.ld
