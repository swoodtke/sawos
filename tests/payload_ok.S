/* SOS M1 unit-A U-mode payload: the happy path (design 140).
 *
 * Hand-written U-mode code, deliberately tiny and free-standing: it touches no
 * memory at all, so the kernel can grant it ONE PMP region (its own bytes, RX)
 * and nothing else — the sharpest possible statement of the M/U split. Every
 * character reaches the console through `debug_putc`; the payload has no UART
 * access and could not print any other way.
 *
 * Rides the `.payload` section — the same appended-blob slot the root sosimg
 * uses from unit B on — so it executes in place, with no loader involved.
 *
 * Syscall ABI (spec §5.7): number in a7, args a0-a5, `ecall`.
 */

    .equ SYS_DEBUG_PUTC, 0
    .equ SYS_EXIT,       1

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      a7, SYS_DEBUG_PUTC
    li      a0, 'S'
    ecall
    li      a0, 'O'
    ecall
    li      a0, 'S'
    ecall
    li      a0, '-'
    ecall
    li      a0, 'U'
    ecall
    li      a0, '\n'
    ecall

    li      a7, SYS_EXIT
    li      a0, 0
    ecall

    /* `exit` does not return. If it ever did, spin inside the granted region
     * rather than run off into memory U-mode cannot read. */
1:
    j       1b
