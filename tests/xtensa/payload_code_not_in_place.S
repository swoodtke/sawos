/* Fixture: a root image whose code is linked in flash, but not at its bytes.
 *
 * `payload_code_in_dram.S` with the R+X segment and the entry linked in the
 * flash instruction window a page past the instruction-window address of the
 * segment's own bytes: what a user script linked for the wrong slot makes,
 * which the build refuses and this fixture is built past. The segment does
 * not name its own bytes, so it is not used in place, and the bus rule takes
 * an executable segment only in place (spec §5.6): the loader refuses the
 * image before anything runs. Were it accepted, it would be copied to an
 * address outside root's region.
 *
 * The image is a data `.payload`, so it lands at the start of root's flash
 * slot, read through the data window. A 64-bit address field is two words,
 * the high one zero.
 */

    /* The instruction-window name of a data-window address. */
    .equ IBUS_FROM_DBUS,  0x42000000 - 0x3C000000
    .equ WRONG_BY,        0x1000
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
    .byte   1                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .4byte  PRIO_MAP                        /* prio_map                    */
    .4byte  0                               /* reserved0                   */
    .4byte  _seg0 + IBUS_FROM_DBUS + WRONG_BY, 0    /* entry           */

    .4byte  _seg0 + IBUS_FROM_DBUS + WRONG_BY, 0    /* load_addr       */
    .4byte  _seg0 - _img_start              /* file_off                    */
    .4byte  _seg0_end - _seg0               /* file_len                    */
    .4byte  _seg0_end - _seg0               /* mem_len                     */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
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
