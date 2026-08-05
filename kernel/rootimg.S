/* SOS kernel <- root image stitching (design 140 unit B).
 *
 * The kernel and the root server are built SEPARATELY — different linker
 * scripts, different load addresses, different toolchain invocations, and on
 * real hardware different flash partitions. This file is how they meet on QEMU
 * `virt`, which has no partition table to point at: the root sosimg is pulled
 * into the kernel ELF as an appended blob, and virt.ld's `.payload` section
 * bounds it with `_payload_start` / `_payload_end` for the loader to find.
 *
 * The blob is DATA, not code — the section is "a", not "ax". The kernel copies
 * each segment to its load address and only then grants execute permission,
 * per the segment's own flags.
 *
 * Assembled with `-I <dir>` naming the directory holding `root.sosimg`, so the
 * same stub stitches any root image the harness builds.
 */

    .section .payload, "a", @progbits
    .balign 16
    .globl _root_image
_root_image:
    .incbin "root.sosimg"
