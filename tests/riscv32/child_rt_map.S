/* Fixture: a child image that names realtime bands, as a case's first child
 * blob.
 *
 * `child_inplace.S` at version 4, whose header's `rt_map` is nonzero: an
 * image that asks for realtime bands, which only root's header is read for
 * (spec 7, 12). The create door refuses it with `BadImage` before anything
 * is placed. Were it accepted, the child would print "SOS-H" and exit as
 * `child_inplace.S`'s does. A 64-bit address field is two words, the high
 * one zero.
 */

    .equ IMAGE_PIECE,     0x100
    .equ BLOB_LEN,        0x8000
    .equ CHILD_MARK,      0x48
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  4
    .equ RT_MAP,          0x07060504
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    /* Syscall ABI (spec 5.7): a0 = handle, a7 = op, args a1-a5; a0 is the
     * status and a1 the value on return. */
    .equ OP_DEBUG_PRINT,  0                 /* SystemOp.DebugPrint         */
    .equ OP_PROCESS_SELF, 2                 /* SystemOp.ProcessSelf        */
    .equ OP_EXIT,         2                 /* ProcessOp.Exit              */
    .equ KEEP_ALL,        -1

    /* The fill below needs the code's length at assembly time, which linker
     * relaxation would leave open. */
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
    .word   RT_MAP                          /* rt_map                      */
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
_code_end:
    .skip   IMAGE_PIECE - (_code_end - _sos_child0_start)
    .skip   BLOB_LEN - IMAGE_PIECE
_sos_child0_end:

    /* The blob row's length, as a symbol the region table can name. */
    .globl _sos_child0_len
    .set    _sos_child0_len, _sos_child0_end - _sos_child0_start
