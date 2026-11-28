/* Fixture: a root image whose realtime map puts a band past the last level.
 *
 * Otherwise well-formed, at the version that names realtime bands. The
 * header's two maps are root's (spec 7, 12): background to high at 0 to 3,
 * and rt0 4, rt1 5, rt2 6 and rt3 8, one past level 7. The loader places the
 * image, then refuses the map and stops the boot, naming the band, before
 * root runs.
 */

    .equ ROOT_ENTRY,      0x80200000
    .equ PRIO_MAP,        0x03020100
    .equ RT_MAP,          0x08060504
    .equ SOSIMG_VERSION,  4
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    .ascii  "SOSI"
    .short  SOSIMG_VERSION
    .byte   1
    .byte   SOSIMG_ARCH
    .word   PRIO_MAP
    .word   RT_MAP
    .quad   ROOT_ENTRY

    .quad   ROOT_ENTRY
    .word   _seg0 - _img_start
    .word   _seg0_end - _seg0
    .word   _seg0_end - _seg0
    .byte   SEG_FLAG_R | SEG_FLAG_X
    .byte   0, 0, 0

    .balign 4
_seg0:
    /* Copied, never run. */
1:
    j       1b
_seg0_end:
