/* Fixture: read-only data used in place at instruction-window addresses.
 *
 * Two segments. The first is root's code, R+X, linked at the
 * instruction-window address of its own bytes, so it is used in place and
 * passes. The second is read-only data, R, linked at the instruction-window
 * address of its own bytes too, so it names its bytes and is in place, but
 * a process reads its data a byte at a time, and the part refuses a byte
 * load in the instruction window. This board's bus rule takes read-only data
 * in place only in the data window (spec §5.6), and the loader refuses the
 * image before anything runs. The emulator serves such a load, so were the
 * image accepted, the program would print "SOS-X" and shut down clean.
 *
 * The image is a data `.payload`, so it lands at the start of root's flash
 * slot, read through the data window. A 64-bit address field is two words,
 * the high one zero.
 */

    /* The instruction-window name of a data-window address. */
    .equ IBUS_FROM_DBUS,  0x42000000 - 0x3C000000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     3                 /* SosimgArch.Xtensa           */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4
    .equ SYSTEM_HANDLE,   1
    .equ OP_DEBUG_PRINT,  0
    .equ OP_SHUTDOWN,     1

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    .ascii  "SOSI"                          /* magic                       */
    .2byte  SOSIMG_VERSION                  /* version                     */
    .byte   2                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .4byte  PRIO_MAP                        /* prio_map                    */
    .4byte  0                               /* reserved0                   */
    .4byte  _seg0 + IBUS_FROM_DBUS, 0       /* entry: the code, in place   */

    .4byte  _seg0 + IBUS_FROM_DBUS, 0       /* load_addr: in place         */
    .4byte  _seg0 - _img_start              /* file_off                    */
    .4byte  _seg0_end - _seg0               /* file_len                    */
    .4byte  _seg0_end - _seg0               /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    .4byte  _seg1 + IBUS_FROM_DBUS, 0       /* load_addr: its own bytes,   */
                                            /* instruction window          */
    .4byte  _seg1 - _img_start              /* file_off                    */
    .4byte  _seg1_end - _seg1               /* file_len                    */
    .4byte  _seg1_end - _seg1               /* mem_len                     */
    .byte   SEG_FLAG_R                      /* flags: read-only            */
    .byte   0, 0, 0                         /* reserved                    */

    .balign 4
_seg0:
    movi    a8, OP_DEBUG_PRINT
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'S'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'O'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'S'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, '-'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'X'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, '\n'
    syscall
    movi    a8, OP_SHUTDOWN
    movi    a2, SYSTEM_HANDLE
    movi    a3, 0
    syscall
1:
    j       1b
    .balign 4
_seg0_end:

_seg1:
    .ascii  "read-only data"
    .balign 4
_seg1_end:
