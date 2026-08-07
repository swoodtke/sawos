/* SOS M1b unit-B fixture: a valid sosimg, assembled by hand (design 162).
 *
 * A SECOND, independent producer of the format Blade emits. Blade's emitter
 * reads an ELF and computes everything; this file states the bytes outright.
 * If the two ever disagree, one of them is wrong and the kernel's loader —
 * which reads both — is the oracle that says so. The riscv32 twin states the
 * SAME 24-byte header and 24-byte record, which is the fixed-width claim: one
 * format, two machines, identical bytes.
 *
 * The image carries one R+X segment: EL0 code that prints "SOS-R" through the
 * System object and exits 0. It is loaded at ROOT_ENTRY, not executed in place,
 * so this also exercises the copy path and the grant derived from the segment
 * flags.
 */

    .equ ROOT_ENTRY,      0x40200000
    /* spec 7 defaults: background 0, low/normal/high 1, low byte first. */
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
    .quad   ROOT_ENTRY                      /* entry (absolute, 64-bit)    */

    /* --- segment table --- */
    .quad   ROOT_ENTRY                      /* load_addr (64-bit, page-aligned) */
    .word   _seg0 - _img_start              /* file_off                    */
    .word   _seg0_end - _seg0               /* file_len                    */
    .word   _seg0_end - _seg0               /* mem_len (no .bss tail)      */
    .byte   SEG_FLAG_R | SEG_FLAG_X         /* flags                       */
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
    mov     x1, #82                 /* 'R' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #10                 /* '\n' */
    svc     #0

    mov     x8, #OP_SHUTDOWN
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #0
    svc     #0

    /* `shutdown` does not return; spin inside the granted region if it ever
     * did. The branch is PC-relative, so it is correct at the load address. */
1:
    b       1b
_seg0_end:
