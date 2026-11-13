/* Fixture: an entry in a segment that does not permit execute.
 *
 * `payload_inplace_rodata.S` with the header's entry at the read-only data
 * segment instead of the code. The entry is judged by the executable
 * segments alone (spec 5.6), so the loader refuses the image before anything
 * runs, though the entry lies in the image's own granted bytes. Were it
 * accepted, root would start at a word of data.
 *
 * A 64-bit address field is two words, the high one zero.
 */

    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    /* Syscall ABI (spec 5.7): a0 = handle, a7 = op, args a1-a5; a0 is the
     * status and a1 the value on return. */
    .equ SYSTEM_HANDLE,   1
    .equ OP_DEBUG_PRINT,  0                 /* SystemOp.DebugPrint         */
    .equ OP_SHUTDOWN,     1                 /* SystemOp.Shutdown           */
    .equ OP_CLOCK_GET,    3                 /* SystemOp.ClockGet           */
    .equ CLOCK_MONOTONIC, 0                 /* ClockType.Monotonic         */
    .equ OP_TIMER_CREATE, 1                 /* ClockOp.TimerCreate         */
    .equ OP_TIMER_ARM,    0                 /* TimerOp.Arm                 */
    .equ ARM_RECORD_LEN,  16                /* two 64-bit words            */

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
    .word   _rodata, 0                      /* entry: the read-only data   */

    /* --- segment table --- */
    .word   _code, 0                        /* load_addr: its own bytes    */
    .word   _code - _img_start              /* file_off                    */
    .word   _code_end - _code               /* file_len                    */
    .word   _code_end - _code               /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    .word   _rodata, 0                      /* load_addr: its own bytes    */
    .word   _rodata - _img_start            /* file_off                    */
    .word   _rodata_end - _rodata           /* file_len                    */
    .word   _rodata_end - _rodata           /* mem_len                     */
    .byte   SEG_FLAG_R                      /* flags: read-only            */
    .byte   0, 0, 0                         /* reserved                    */

    /* --- segment 0: the U-mode program --- */
    .balign 4
_code:
    li      a7, OP_CLOCK_GET
    li      a0, SYSTEM_HANDLE
    li      a1, CLOCK_MONOTONIC
    ecall
    mv      a0, a1                          /* the clock                   */
    li      a7, OP_TIMER_CREATE
    ecall
    mv      a0, a1                          /* the timer                   */
    li      a7, OP_TIMER_ARM
    la      a1, _arm_record
    li      a2, ARM_RECORD_LEN
    ecall

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
    li      a1, 'T'
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
_code_end:

    /* --- segment 1: read-only data, the arm record --- */
    .balign 8
_rodata:
_arm_record:
    .word   1000000000, 0                   /* after: one second           */
    .word   0, 0                            /* interval: none              */
_rodata_end:
