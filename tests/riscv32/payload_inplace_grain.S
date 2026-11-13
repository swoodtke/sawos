/* Fixture: a segment already where it is linked, off the grant grain.
 *
 * `payload_inplace.S` with the segment starting two bytes past a word, which
 * the compressed instructions allow it to run at. A grant covers whole units
 * of the protection grain, so the row would also grant the two bytes before
 * the segment, and the loader refuses the image (spec 5.6). Were it accepted,
 * the program would print "SOS-I" and shut down clean.
 *
 * A 64-bit address field is two words, the low one a relocation and the high
 * one zero: a 32-bit assembler has no 8-byte relocation to point at a symbol
 * with, and the bytes are the same little-endian.
 */

    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    /* Syscall ABI (spec 5.7): a0 = handle, a7 = op, args a1-a5. */
    .equ SYSTEM_HANDLE,   1
    .equ OP_DEBUG_PRINT,  0
    .equ OP_SHUTDOWN,     1

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    /* --- header --- */
    .ascii  "SOSI"                          /* magic                       */
    .short  SOSIMG_VERSION                  /* version                     */
    .byte   1                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .word   PRIO_MAP                        /* prio_map                    */
    .word   0                               /* reserved0 (v3 padding)      */
    .word   _seg0, 0                        /* entry: the segment's own    */

    /* --- segment table --- */
    .word   _seg0, 0                        /* load_addr: its own bytes    */
    .word   _seg0 - _img_start              /* file_off                    */
    .word   _seg0_end - _seg0               /* file_len                    */
    .word   _seg0_end - _seg0               /* mem_len (no .bss tail)      */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    /* --- segment 0: the U-mode program --- */
    .balign 4
    .2byte  0                               /* two bytes off the grain     */
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
    li      a1, 'I'
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
    .balign 4
_seg0_end:
