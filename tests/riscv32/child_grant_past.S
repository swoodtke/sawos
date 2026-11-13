/* Fixture: a child image whose segment used in place is granted past it.
 *
 * Linked as a case's `child_asm`, so the region table's first blob row names
 * `_sos_child0_start` and `_sos_child0_len`, and the blob is the image and
 * nothing more. Its one R+X segment is linked at the address of its own
 * bytes, so it is used in place, and it is the image's last bytes: it ends
 * two bytes past a word, and so does the blob. A grant covers whole units of
 * the protection grain, so the row would reach two bytes the image does not
 * hold, and `process_create` refuses the image with `BadImage` (spec 5.6).
 * Were it accepted, the child would print "SOS-H" and exit as
 * `child_inplace.S`'s does.
 *
 * A 64-bit address field is two words, the high one zero.
 */

    .equ CHILD_MARK,      0x48
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4
    .equ OP_DEBUG_PRINT,  0                 /* SystemOp.DebugPrint         */
    .equ OP_PROCESS_SELF, 2                 /* SystemOp.ProcessSelf        */
    .equ OP_EXIT,         2                 /* ProcessOp.Exit              */
    .equ KEEP_ALL,        -1

    /* The blob's length is fixed at assembly time, which linker relaxation
     * would leave open. */
    .option norelax

    .section .childimg, "a", @progbits
    .balign 16
    .globl _sos_child0_start
    .globl _sos_child0_end
_sos_child0_start:
    .ascii  "SOSI"                          /* magic                       */
    .short  SOSIMG_VERSION                  /* version                     */
    .byte   1                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .word   PRIO_MAP                        /* prio_map                    */
    .word   0                               /* reserved0                   */
    .word   _code, 0                        /* entry: the segment's own    */

    .word   _code, 0                        /* load_addr: its own bytes    */
    .word   _code - _sos_child0_start       /* file_off                    */
    .word   _code_end - _code               /* file_len                    */
    .word   _code_end - _code               /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    .balign 4
_code:
    mv      s0, a0                          /* the System handle           */
    li      a7, OP_DEBUG_PRINT
    mv      a0, s0
    li      a1, 'S'
    ecall
    mv      a0, s0
    li      a1, 'O'
    ecall
    mv      a0, s0
    li      a1, 'S'
    ecall
    mv      a0, s0
    li      a1, '-'
    ecall
    mv      a0, s0
    li      a1, 'H'
    ecall
    mv      a0, s0
    li      a1, '\n'
    ecall

    li      a7, OP_PROCESS_SELF
    mv      a0, s0
    li      a1, KEEP_ALL
    ecall
    mv      a0, a1                          /* this process's handle       */
    li      a7, OP_EXIT
    li      a1, CHILD_MARK
    ecall
1:
    j       1b
    .balign 4
    .2byte  0                               /* two bytes off the grain     */
_code_end:
_sos_child0_end:

    /* The blob row's length, as a symbol the region table can name. */
    .globl _sos_child0_len
    .set    _sos_child0_len, _sos_child0_end - _sos_child0_start
