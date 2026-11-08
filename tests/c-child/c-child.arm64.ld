/* The C child's linker script, arm64.
 *
 * Same two lines as the riscv32 sibling (see it for the reasoning), against
 * this board's shared script. There are two files because a package names
 * one `linker-script` per target triple.
 *
 * `user.ld` is one script for every process on this board, so nothing here
 * is about being a child: the base is the canonical `hal.USER_IMAGE_BASE`
 * and the kernel maps this image's frames there.
 */

ARENA_SIZE = 0;

INCLUDE ../../hal/arm64/user/user.ld
