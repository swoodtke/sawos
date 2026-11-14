/* Fixture: a root image two of whose segments meet.
 *
 * Segment 0 is `payload_sosimg.S`'s program, R+X at ROOT_ENTRY and longer than
 * three grains. Segment 1 is a zero-filled R+W segment linked two grains above
 * ROOT_ENTRY, inside segment 0, so the two rows would grant those bytes twice.
 * No two rows of one image may meet (spec 5.6), so the loader stops the boot
 * before a byte is copied, naming segment 1's address. Were the image
 * accepted, root would print "SOS-M" and shut down clean.
 */

    .equ ROOT_ENTRY,      0x80200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_W,      2
    .equ SEG_FLAG_X,      4
    .equ GRAIN,           4                 /* hal.PROT_GRAIN              */
    .equ DATA_LEN,        16

    .equ SYSTEM_HANDLE,   1
    .equ OP_DEBUG_PRINT,  0
    .equ OP_SHUTDOWN,     1

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    /* --- header --- */
    .ascii  "SOSI"                          /* magic                       */
    .short  SOSIMG_VERSION                  /* version                     */
    .byte   2                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .word   PRIO_MAP                        /* prio_map                    */
    .word   0                               /* reserved0                   */
    .quad   ROOT_ENTRY                      /* entry (absolute, 64-bit)    */

    /* --- segment table --- */
    .quad   ROOT_ENTRY                      /* load_addr                   */
    .word   _seg0 - _img_start              /* file_off                    */
    .word   _seg0_end - _seg0               /* file_len                    */
    .word   _seg0_end - _seg0               /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    /* Two grains into segment 0. */
    .quad   ROOT_ENTRY + 2 * GRAIN
    .word   0                               /* file_off                    */
    .word   0                               /* file_len                    */
    .word   DATA_LEN                        /* mem_len: zero-filled        */
    .byte   SEG_FLAG_R | SEG_FLAG_W         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    /* --- segment 0: the U-mode program --- */
    .balign 4
_seg0:
    li      a7, OP_DEBUG_PRINT
    li      a0, SYSTEM_HANDLE
    li      a1, 'S'
    ecall
    li      a0, SYSTEM_HANDLE
    li      a1, 'O'
    ecall
    li      a0, SYSTEM_HANDLE
    li      a1, 'S'
    ecall
    li      a0, SYSTEM_HANDLE
    li      a1, '-'
    ecall
    li      a0, SYSTEM_HANDLE
    li      a1, 'M'
    ecall
    li      a0, SYSTEM_HANDLE
    li      a1, '\n'
    ecall

    li      a7, OP_SHUTDOWN
    li      a0, SYSTEM_HANDLE
    li      a1, 0
    ecall

1:
    j       1b
_seg0_end:
