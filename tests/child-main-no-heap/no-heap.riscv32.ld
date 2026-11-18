/* No arena, riscv32: the shared script `PROVIDE`s the default, and this
 * assignment wins over it. `tests/arena-small/arena-small.riscv32.ld` says
 * how a package's own script reaches the shared one.
 */
ARENA_SIZE = 0;

INCLUDE ../../hal/riscv32/user/user.ld
