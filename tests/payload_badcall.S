/* SOS M1 unit-A U-mode payload: an unknown syscall number (design 140).
 *
 * A well-formed `ecall` carrying a number outside the v1 table. The kernel must
 * reject it with a cause tag and FAIL, rather than index off the end of its
 * dispatch or return a bogus success.
 */

    .equ SYS_UNASSIGNED, 99

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      a7, SYS_UNASSIGNED
    ecall

    /* Unreachable: the kernel does not return from a bad syscall. */
1:
    j       1b
