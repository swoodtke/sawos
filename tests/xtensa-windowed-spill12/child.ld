/* A C child with no Saw runtime, so no arena, and the act `child.c`
 * performs: it spills a call12 frame below 0x10000010. */
ARENA_SIZE = 0;
WINDOWED_CHILD_ACT = 5;

INCLUDE ../../hal/xtensa-esp32s3/user/user.ld
