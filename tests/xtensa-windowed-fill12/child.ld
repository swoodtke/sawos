/* A C child with no Saw runtime, so no arena, and the act `child.c`
 * performs: it fills a call12 frame from below 0x10000010. */
ARENA_SIZE = 0;
WINDOWED_CHILD_ACT = 8;

INCLUDE ../../hal/xtensa-esp32s3/user/child.ld
