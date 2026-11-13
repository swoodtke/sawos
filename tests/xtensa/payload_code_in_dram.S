/* Fixture: a root image whose code is linked in DRAM, to be copied there.
 *
 * One R+X segment linked at the base of root's region, at a DRAM address,
 * with the entry at its start. Every other board copies such a segment and
 * runs it. This part fetches no instruction at a DRAM address
 * (../../hal/xtensa-esp32s3/ABI.md §3c), so its bus rule takes an executable
 * segment only in place in the flash instruction window (spec §5.6), and the
 * loader refuses the image before anything runs. The emulator fetches there,
 * so were the image accepted, the program would print "SOS-X" and shut down
 * clean.
 *
 * The image is a data `.payload`, so it lands at the start of root's flash
 * slot, read through the data window. A 64-bit address field is two words,
 * the high one zero. Syscall ABI (spec §5.7): a2 = handle, a8 = op, args
 * a3-a5.
 */

    .equ CODE_AT,         0x3FCB0000        /* hal.ROOT_LOAD_BASE (DRAM)   */
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
    .4byte  CODE_AT, 0                      /* entry                       */

    .4byte  CODE_AT, 0                      /* load_addr: DRAM             */
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
