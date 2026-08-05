/* SOS M1 unit-B fixture: a valid sosimg, assembled by hand (design 140).
 *
 * A SECOND, independent producer of the format Blade emits. Blade's emitter
 * reads an ELF and computes everything; this file states the bytes outright.
 * If the two ever disagree, one of them is wrong and the kernel's loader —
 * which reads both — is the oracle that says so.
 *
 * The image carries one R+X segment: U-mode code that prints "SOS-R" through
 * `debug_putc` and exits 0. It is loaded at ROOT_ENTRY, not executed in place,
 * so this also exercises the copy path and the PMP grant derived from the
 * segment flags.
 */

    .equ ROOT_ENTRY,      0x80200000
    /* spec 7 defaults: background 0, low/normal/high 1, low byte first. */
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  1
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    .equ SYS_DEBUG_PUTC,  0
    .equ SYS_EXIT,        1

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    /* --- header --- */
    .ascii  "SOSI"                          /* magic                       */
    .short  SOSIMG_VERSION                  /* version                     */
    .byte   1                               /* seg_count                   */
    .byte   0                               /* reserved                    */
    .word   ROOT_ENTRY                      /* entry (absolute)            */
    .word   PRIO_MAP                        /* prio_map                    */

    /* --- segment table --- */
    .word   _seg0 - _img_start              /* file_off                    */
    .word   ROOT_ENTRY                      /* load_addr                   */
    .word   _seg0_end - _seg0               /* file_len                    */
    .word   _seg0_end - _seg0               /* mem_len (no .bss tail)      */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    /* --- segment 0: the U-mode program --- */
    .balign 4
_seg0:
    li      a7, SYS_DEBUG_PUTC
    li      a0, 'S'
    ecall
    li      a0, 'O'
    ecall
    li      a0, 'S'
    ecall
    li      a0, '-'
    ecall
    li      a0, 'R'
    ecall
    li      a0, '\n'
    ecall

    li      a7, SYS_EXIT
    li      a0, 0
    ecall

    /* `exit` does not return; spin inside the granted region if it ever did.
     * The branch is PC-relative, so it is correct at the load address too. */
1:
    j       1b
_seg0_end:
