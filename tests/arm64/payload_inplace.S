/* Fixture: a root image whose one segment names its own bytes.
 *
 * The riscv32 twin's image: the segment's load address and the entry are the
 * address of its own bytes, stated through a relocation, here the physical
 * address the kernel holds the image's bytes at, so the two are one number.
 * This board uses no segment in place (spec 5.5: a user address names what a
 * process's own tables map, and the kernel maps no image page), so the
 * segment is copied like any other and judged against root's region, which
 * the kernel's own memory is below, and the loader refuses the image. Were
 * the segment used in place, root's tables would grant the payload's own page
 * at that address, and the program would print "SOS-I" and shut down clean.
 */

    /* The kernel's linear map: a kernel address less this is the
     * physical address the loader holds the payload by (`virt.ld`). */
    .equ LINMAP_OFFSET,   0xFFFFFF8000000000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_VERSION,  3
    .equ SOSIMG_ARCH,     2                 /* SosimgArch.Arm64            */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    /* Syscall ABI (spec 5.7): x0 = handle, x8 = op, args x1-x5. */
    .equ SYSTEM_HANDLE,   1
    .equ OP_DEBUG_PRINT,  0
    .equ OP_SHUTDOWN,     1

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    /* --- header --- */
    .ascii  "SOSI"                          /* magic                       */
    .short  SOSIMG_VERSION                  /* version                     */
    .byte   1                               /* seg_count                   */
    .byte   SOSIMG_ARCH                     /* arch                        */
    .word   PRIO_MAP                        /* prio_map                    */
    .word   0                               /* reserved0 (v3 padding)      */
    .quad   _seg0 - LINMAP_OFFSET           /* entry: its bytes, physical  */

    /* --- segment table --- */
    .quad   _seg0 - LINMAP_OFFSET           /* load_addr: the same         */
    .word   _seg0 - _img_start              /* file_off                    */
    .word   _seg0_end - _seg0               /* file_len                    */
    .word   _seg0_end - _seg0               /* mem_len (no .bss tail)      */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
    .byte   0, 0, 0                         /* reserved                    */

    /* --- segment 0: the EL0 program, on a page of its own, since a page is
     * this board's grant grain and a segment off it is refused first --- */
    .balign 4096
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
    mov     x1, #73                 /* 'I' */
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
