/* Stitches the root image into the kernel image.
 *
 * The kernel and the root server are built separately (different linker
 * scripts and load addresses). QEMU `virt` has no partition table to point at,
 * so the root sosimg is pulled into the kernel ELF as an appended blob, and
 * virt.ld's `.payload` section bounds it with `_payload_start` /
 * `_payload_end` for the loader.
 *
 * The blob is data, not code: the section is "a", not "ax". The loader copies
 * each segment to its load address and only then grants execute permission per
 * the segment's own flags.
 *
 * Assemble with `-I <dir>` naming the directory that holds `root.sosimg`, so
 * the same stub stitches any root image.
 */

    .section .payload, "a", @progbits
    .balign 16
    .globl _root_image
_root_image:
    .incbin "root.sosimg"
