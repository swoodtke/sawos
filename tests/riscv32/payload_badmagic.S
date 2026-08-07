/* SOS M1 unit-B fixture: a root image with the wrong magic (design 140).
 *
 * Otherwise well-formed. The loader must reject it on the first field it reads
 * and FAIL with a diagnostic, without placing a byte or entering U-mode.
 */

    .equ ROOT_ENTRY,      0x80200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  2
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    .ascii  "NOPE"                          /* not 'SOSI'                  */
    .short  SOSIMG_VERSION
    .byte   1
    .byte   SOSIMG_ARCH
    .word   ROOT_ENTRY
    .word   PRIO_MAP

    .word   _seg0 - _img_start
    .word   ROOT_ENTRY
    .word   _seg0_end - _seg0
    .word   _seg0_end - _seg0
    .byte   SEG_FLAG_R | SEG_FLAG_X
    .byte   0, 0, 0

    .balign 4
_seg0:
    /* Never reached, never copied. */
1:
    j       1b
_seg0_end:
