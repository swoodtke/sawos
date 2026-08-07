/* SOS M1b unit-A user-mode payload: an illegal access (design 162).
 *
 * Stores to the kernel's own text base. EL0 holds no grant for that page — the
 * identity map gives it EL1 access only — so the store raises a data abort from
 * a lower EL (ESR EC 0x24) and the kernel must diagnose it and FAIL the run:
 * never hang, never let the write land.
 *
 * The kernel reports it as `store-access-fault`, the same name Profile A uses
 * for the same event, which is why the harness asserts one string against both
 * architectures.
 */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    movz    x0, #0x4000, lsl #16    /* 0x4000_0000 — kernel .text base */
    str     xzr, [x0]

    /* Unreachable: the store above traps. */
1:
    b       1b
