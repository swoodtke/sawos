/* Per-package arena override, riscv32: a small arena, then the shared child
 * script. `tests/arena-small` explains the mechanism.
 *
 * The image stays small so that a front piece cut off the child's region can
 * hold every segment and none of the stack.
 */
ARENA_SIZE = 4K;

INCLUDE ../../hal/riscv32/user/child.ld
