/* Fixture: a root image of version 3 whose realtime map is not zero.
 *
 * Otherwise well-formed, every level in range. Version 3 is the version of an
 * image that names no realtime band, so its `rt_map` word must be zero: the
 * loader refuses the version before it places anything.
 */

    .equ ROOT_ENTRY,      0x40200000
    .equ PRIO_MAP,        0x03020100
    .equ RT_MAP,          0x07060504
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     2                 /* SosimgArch.Arm64            */
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
    b       1b
_seg0_end:
