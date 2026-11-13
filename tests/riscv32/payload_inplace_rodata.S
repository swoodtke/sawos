/* Fixture: a system call's buffer in read-only data used in place.
 *
 * Two segments, each linked at the address of its own bytes, so the loader
 * uses both in place (spec 5.6): root's code, R+X, and its read-only data, R,
 * which holds a Timer's arm record. Neither lies in root's region, which
 * holds only the stack. The program gets a Monotonic clock, makes a Timer
 * and arms it with the record where it sits, so the copy-in door reads its
 * source out of the image's own bytes; the door takes the read-only segments
 * used in place as well as the region (spec 2.2). Then it prints "SOS-T" and
 * shuts down with 0. Were the source refused, the kernel would end root with
 * `BadBuffer` before the line.
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
    .word   _code, 0                        /* entry: the code's own       */

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
