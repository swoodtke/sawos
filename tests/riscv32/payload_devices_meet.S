/* Fixture: a root image that declares the board's device window twice.
 *
 * Segment 0 is `payload_sosimg.S`'s program, R+X at ROOT_ENTRY. Segments 1 and
 * 2 are device records naming the one window the board offers a process, the
 * console UART's page, so their rows would grant that page twice. No two rows
 * of one image may meet (spec 5.6), so the loader stops the boot before a byte
 * is copied, naming segment 2's address. Were the image accepted, root would
 * print "SOS-M" and shut down clean.
 */

    .equ ROOT_ENTRY,      0x80200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32          */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_W,      2
    .equ SEG_FLAG_X,      4
    .equ GRAIN,           4                 /* hal.PROT_GRAIN              */
    .equ SEG_FLAG_DEV,    8
    .equ UART_PAGE,       0x10000000        /* hal.DEVICE_GRANT_BASE       */
    .equ UART_LEN,        0x1000            /* hal.DEVICE_GRANT_LEN        */

    .equ SYSTEM_HANDLE,   1
    .equ OP_DEBUG_PRINT,  0
    .equ OP_SHUTDOWN,     1

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    /* --- header --- */
    .ascii  "SOSI"                          /* magic                       */
    .short  SOSIMG_VERSION                  /* version                     */
    .byte   3                               /* seg_count                   */
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

    /* The device window, twice. */
    .quad   UART_PAGE                       /* load_addr                   */
    .word   0                               /* file_off                    */
    .word   0                               /* file_len                    */
    .word   UART_LEN                        /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_W | SEG_FLAG_DEV
    .byte   0, 0, 0                         /* reserved                    */

    .quad   UART_PAGE                       /* load_addr                   */
    .word   0                               /* file_off                    */
    .word   0                               /* file_len                    */
    .word   UART_LEN                        /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_W | SEG_FLAG_DEV
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
