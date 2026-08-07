/* SOS M1b fixture: a well-formed image for the OTHER profile (design 162).
 *
 * Magic, version, segment table and permissions are all correct; the only thing
 * wrong with this image is that its code is not this machine's. Before the
 * format carried an arch tag the two profiles' headers were byte-compatible
 * wrappers around incompatible instructions, and the only thing that stopped
 * one booting on the other was that nobody had tried it. The loader must refuse
 * it on the tag, before copying a byte or entering user mode.
 *
 * The segment carries no instructions at all — it is never copied, so there is
 * nothing to encode, and that keeps the fixture about the tag and nothing else.
 */

    .equ ROOT_ENTRY,      0x40200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32 — not ours */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    .ascii  "SOSI"
    .short  SOSIMG_VERSION
    .byte   1
    .byte   SOSIMG_ARCH                     /* the hostile field           */
    .word   PRIO_MAP
    .word   0
    .quad   ROOT_ENTRY

    .quad   ROOT_ENTRY
    .word   _seg0 - _img_start
    .word   _seg0_end - _seg0
    .word   _seg0_end - _seg0
    .byte   SEG_FLAG_R | SEG_FLAG_X
    .byte   0, 0, 0

    .balign 4
_seg0:
    /* Another machine's code would go here. Never copied, never run. */
    .word   0
    .word   0
_seg0_end:
