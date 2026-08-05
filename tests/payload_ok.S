/* SOS M1 unit-A U-mode payload: the happy path (design 140).
 *
 * Hand-written U-mode code, deliberately tiny and free-standing: it touches no
 * memory at all, so the kernel can grant it ONE PMP region (its own bytes, RX)
 * and nothing else — the sharpest possible statement of the M/U split. Every
 * character reaches the console through a System op; the payload has no UART
 * access and could not print any other way.
 *
 * Rides the `.payload` section — the same appended-blob slot the root sosimg
 * uses from unit B on — so it executes in place, with no loader involved.
 *
 * Syscall ABI (spec §5.7): a0 = HANDLE, a7 = OP, args a1-a5, `ecall`; returns
 * a0 = status, a1 = value. Every syscall is an object op — the handle in a0 is
 * not decoration, it is the authority. a0 is clobbered by the return, so each
 * call reloads it.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in a0 */
    .equ OP_DEBUG_PRINT,    0
    .equ OP_SHUTDOWN,       1

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
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
    li      a1, 'U'
    ecall
    li      a0, SYSTEM_HANDLE
    li      a1, '\n'
    ecall

    li      a7, OP_SHUTDOWN
    li      a0, SYSTEM_HANDLE
    li      a1, 0
    ecall

    /* `shutdown` does not return. If it ever did, spin inside the granted
     * region rather than run off into memory U-mode cannot read. */
1:
    j       1b
