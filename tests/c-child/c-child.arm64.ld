/* THE C CHILD'S SCRIPT (sawos design 39) — arm64, Profile B.
 *
 * The riscv32 sibling beside this file carries the argument; this is the same
 * two lines against the other profile's shared script. That there IS a second
 * file is the manifest's doing rather than the mechanism's — a package names
 * one `linker-script` per target triple — exactly as `tests/arena-small/`
 * records.
 *
 * `user.ld` is ONE script for every process on this board (design 33), so
 * nothing here is about being a child: the base is the canonical
 * `hal.USER_IMAGE_BASE` and the kernel maps this image's frames there.
 */

ARENA_SIZE = 0;

INCLUDE ../../hal/arm64/user/user.ld
