/* SOS M1b unit-B fixture: a root image aimed at the kernel (design 162).
 *
 * The header is valid and the magic is right; the segment asks to be loaded at
 * the kernel's own text base. This is the check that matters most — a loader
 * that trusted `load_addr` would let any image overwrite the kernel before the
 * page tables ever came into it. The loader must reject the image before
 * copying, and FAIL with a diagnostic naming the address.
 */

    .equ KERNEL_BASE,     0x40000000        /* NOT inside the root region  */
    .equ ROOT_ENTRY,      0x40200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     2                 /* SosimgArch.Arm64            */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_W,      2

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    .ascii  "SOSI"
    .short  SOSIMG_VERSION
    .byte   1
    .byte   SOSIMG_ARCH
    .word   PRIO_MAP
    .word   0
    .quad   ROOT_ENTRY

    .quad   KERNEL_BASE                     /* the hostile field           */
    .word   _seg0 - _img_start
    .word   _seg0_end - _seg0
    .word   _seg0_end - _seg0
    .byte   SEG_FLAG_R | SEG_FLAG_W
    .byte   0, 0, 0

    .balign 4
_seg0:
    /* What the image would have written over the kernel. Never copied. */
    .word   0xDEADBEEF
    .word   0xDEADBEEF
_seg0_end:
