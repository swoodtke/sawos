/* Fixture: a root image that declares the board's device window twice.
 *
 * Segment 0 is `payload_sosimg.S`'s program, R+X at ROOT_ENTRY. Segments 1 and
 * 2 are device records naming the one window the board offers a process, the
 * console UART's page, so their rows would grant that page twice. No two rows
 * of one image may meet (spec 5.6), so the loader stops the boot before a byte
 * is copied, naming segment 2's address. Were the image accepted, root would
 * print "SOS-M" and shut down clean.
 */

    .equ ROOT_ENTRY,      0x40200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     2                 /* SosimgArch.Arm64            */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_W,      2
    .equ SEG_FLAG_X,      4
    .equ GRAIN,           0x1000            /* hal.PROT_GRAIN              */
    .equ SEG_FLAG_DEV,    8
    .equ UART_PAGE,       0x09000000        /* hal.DEVICE_GRANT_BASE       */
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
    .word   3 * GRAIN                       /* mem_len: a zero-filled tail */
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

    /* --- segment 0: the EL0 program --- */
    .balign 4
_seg0:
    mov     x8, #OP_DEBUG_PRINT
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #83                 /* 'S' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #79                 /* 'O' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #83                 /* 'S' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #45                 /* '-' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #77                 /* 'M' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #10                 /* '\n' */
    svc     #0

    mov     x8, #OP_SHUTDOWN
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #0
    svc     #0

1:
    b       1b
_seg0_end:
