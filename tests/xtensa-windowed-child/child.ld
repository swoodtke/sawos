/* A C child with no Saw runtime, so no arena, and the act `child.c`
 * performs: it exits with code 5 from 30 frames deep. */
ARENA_SIZE = 0;
WINDOWED_CHILD_ACT = 1;

INCLUDE ../../hal/xtensa-esp32s3/user/child.ld
