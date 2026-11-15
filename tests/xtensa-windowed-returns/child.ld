/* A C child with no Saw runtime, so no arena, and the act `child.c`
 * performs: it returns from its entry after its first pane was spilled. */
ARENA_SIZE = 0;
WINDOWED_CHILD_ACT = 2;

INCLUDE ../../hal/xtensa-esp32s3/user/child.ld
