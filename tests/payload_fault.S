/* SOS M1 unit-A U-mode payload: an illegal access (design 140).
 *
 * Stores to the kernel's own text base. U-mode holds no PMP grant for that
 * range, so the store raises a store/AMO access fault (mcause 7) and the kernel
 * must diagnose it and FAIL the run — never hang, never let the write land.
 */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      t0, 0x80000000          /* kernel .text base — not granted to U */
    li      t1, 0
    sw      t1, 0(t0)

    /* Unreachable: the store above traps. */
1:
    j       1b
