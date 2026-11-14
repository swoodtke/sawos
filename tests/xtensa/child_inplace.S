/* Fixture: a child image used in place, as a case's first child blob.
 *
 * The riscv32 twin's image on this board. The runner links this object in
 * place of a Blade-built child (a case's `child_asm`), so `.childimg` puts it
 * at the start of the child's flash slot, read through the data window, and
 * the region table's first blob row names `_sos_child0_start` and
 * `_sos_child0_len`. The header and one R+X segment come first, the segment
 * linked at the instruction-window address of its own bytes, so the loader
 * uses it in place there (spec 5.6); the child's only other row is the stack
 * the kernel grants in its destination. The blob is zero-filled behind the
 * image to `BLOB_LEN`, so a launcher whose blob row is a pool can allocate the
 * image off its front (`IMAGE_PIECE` bytes) and find filler in the allocation
 * after it: `tests/image-held` and `tests/flash-region` rely on both
 * lengths.
 *
 * The program prints "SOS-H" through the System handle its launcher gave it,
 * derives its own Process handle and exits with `CHILD_MARK`. It touches no
 * memory and loads no literal. A 64-bit address field is two words, the high
 * one zero.
 */

    /* The instruction-window name of a data-window address. */
    .equ IBUS_FROM_DBUS,  0x42000000 - 0x3C000000
    .equ IMAGE_PIECE,     0x100
    .equ BLOB_LEN,        0x8000
    .equ CHILD_MARK,      0x48
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     3                 /* SosimgArch.Xtensa           */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    /* Syscall ABI (spec 5.7): a2 = handle, a8 = op, args a3-a5; a2 is the
     * status and a3 the value on return. */
    .equ OP_DEBUG_PRINT,  0                 /* SystemOp.DebugPrint         */
    .equ OP_PROCESS_SELF, 2                 /* SystemOp.ProcessSelf        */
    .equ OP_EXIT,         2                 /* ProcessOp.Exit              */
    .equ KEEP_ALL,        -1

    .section .childimg, "a", @progbits
    .balign 16
    .globl _sos_child0_start
    .globl _sos_child0_end
_sos_child0_start:
    .ascii  "SOSI"                          /* magic                       */
    .2byte  SOSIMG_VERSION                  /* version                     */
    .byte   1                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .4byte  PRIO_MAP                        /* prio_map                    */
    .4byte  0                               /* reserved0                   */
    .4byte  _code + IBUS_FROM_DBUS, 0       /* entry: the code, in place   */

    .4byte  _code + IBUS_FROM_DBUS, 0       /* load_addr: in place         */
    .4byte  _code - _sos_child0_start       /* file_off                    */
    .4byte  _code_end - _code               /* file_len                    */
    .4byte  _code_end - _code               /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    .balign 4
_code:
    mov     a12, a2                         /* the System handle           */
    movi    a8, OP_DEBUG_PRINT
    mov     a2, a12
    movi    a3, 'S'
    syscall
    mov     a2, a12
    movi    a3, 'O'
    syscall
    mov     a2, a12
    movi    a3, 'S'
    syscall
    mov     a2, a12
    movi    a3, '-'
    syscall
    mov     a2, a12
    movi    a3, 'H'
    syscall
    mov     a2, a12
    movi    a3, '\n'
    syscall

    movi    a8, OP_PROCESS_SELF
    mov     a2, a12
    movi    a3, KEEP_ALL
    syscall
    mov     a2, a3                          /* this process's handle       */
    movi    a8, OP_EXIT
    movi    a3, CHILD_MARK
    syscall
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
