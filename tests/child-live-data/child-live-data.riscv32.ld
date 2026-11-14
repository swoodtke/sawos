/* Per-package arena override, riscv32: a small arena, then the shared child
 * script. `tests/arena-small` explains the mechanism.
 *
 * The image stays small so that every segment lies in the front of the
 * child's destination, the bytes `tests/destination-held` looks through.
 */
ARENA_SIZE = 4K;

INCLUDE ../../hal/riscv32/user/child.ld
