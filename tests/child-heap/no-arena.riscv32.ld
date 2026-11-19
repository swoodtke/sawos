/* No arena, riscv32: the shared script `PROVIDE`s the default, and
 * this assignment wins over it.
 */
ARENA_SIZE = 0;

INCLUDE ../../hal/riscv32/user/user.ld
