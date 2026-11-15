/* A C child with no Saw runtime, so no arena, and the act `child.c`
 * performs: it spills a call4 frame below 0x10000010. */
ARENA_SIZE = 0;
WINDOWED_CHILD_ACT = 3;

INCLUDE ../../hal/xtensa-esp32s3/user/child.ld
