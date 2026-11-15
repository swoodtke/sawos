/* User-mode payload: points EXCSAVE1 at nothing, then traps, on Xtensa.
 *
 * EXCSAVE1 is the running thread's frame address while user code runs, and
 * nothing in the core keeps user code from writing it (ABI.md §5e). The
 * `syscall` that follows makes the user vector's first store, of a0 into the
 * frame, go to 0x10000008, past the unmapped probe address: a fault with
 * PS.EXCM = 1, so a double exception, with PS.UM = 1 and DEPC in the user
 * vector, not in a window handler. The double vector must take that as the
 * kernel's and stop the machine, rather than enter the trap entry with the
 * same frame again. */

    .section .payload, "ax"
    .balign 4
    .globl _umode_payload
_umode_payload:
    movi    a3, 1
    slli    a3, a3, 28              /* 0x10000000 */
    wsr     a3, excsave1
    syscall
1:
    j       1b
