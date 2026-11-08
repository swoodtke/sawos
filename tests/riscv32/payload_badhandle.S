/* U-mode payload: an op on a handle the process never held.
 *
 * A caller error the process could have checked (it knows which handles it
 * holds) terminates it. The kernel reports the reason, tears down everything
 * the process owned, and stays up.
 *
 * This is a payload and not a root server written in Saw because the `sos`
 * module's typed layer has no way to build a `Thread` from a word, so "a handle
 * I was never given" cannot be spelled in Saw. The kernel's validation is
 * tested at the altitude where raw handles live, beside `payload_badcall.S`,
 * which tests the bad-op half the same way.
 *
 * The payload names no op number that could go stale: dispatch resolves the
 * handle before it decodes the op (spec §3: table lookup, object type, op
 * table, rights), so an unbound handle is rejected with the op word never
 * read. Op 0 is as good as any.
 */

    .equ NEVER_HELD_HANDLE, 42      /* a handle the boot set never made */
    .equ ANY_OP,            0       /* never decoded; handle rejected first */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      a7, ANY_OP
    li      a0, NEVER_HELD_HANDLE
    li      a1, 0
    ecall

    /* Unreachable. If the kernel ever answered a bad handle with a status, we
     * would arrive here: spin inside the granted region and let the harness
     * fail on the timeout rather than on a clean exit that looks like
     * success. */
1:
    j       1b
