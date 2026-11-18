/* A 176 KiB arena, so blocks past 64 KiB, in more than one of the heap's
 * classes, fit beside each other. `tests/arena-small/arena-small.riscv32.ld`
 * explains the override.
 */
ARENA_SIZE = 176K;

INCLUDE ../../hal/riscv32/user/user.ld
