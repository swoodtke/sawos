/* SOS U-mode payload: an op on a handle the process never held.
 *
 * Design 178's faults ruling: a caller error the process could have CHECKED —
 * and it knows which handles it holds — terminates it. The kernel reports the
 * reason, tears down everything the process owned, and stays up.
 *
 * WHY THIS IS A PAYLOAD AND NOT A ROOT SERVER WRITTEN IN SAW. It used to be
 * one, and the second review round is what moved it: the `sos` module's typed
 * layer now has no way to build a `Thread` from a word, so "a handle I was
 * never given" is not a thing a Saw process can spell. That is the guarantee
 * working, and it means the test of the kernel's validation belongs at the
 * altitude where raw handles legitimately live — beside `payload_badcall.S`,
 * which tests the bad-OP half the same way.
 *
 * DRIFT-PROOF BY CONSTRUCTION. Dispatch resolves the HANDLE before it decodes
 * the op (spec §3's order: table lookup -> object type -> op table -> rights),
 * so an unbound handle is rejected with the op word never looked at. This
 * payload therefore names no op number that could go stale — op 0 is as good as
 * any, because nothing ever reads it.
 */

    .equ NEVER_HELD_HANDLE, 42      /* the boot set is three wide and made none */
    .equ ANY_OP,            0       /* never decoded — the handle is rejected first */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      a7, ANY_OP
    li      a0, NEVER_HELD_HANDLE
    li      a1, 0
    ecall

    /* Unreachable under the ruling. If the kernel ever answered a bad handle
     * with a status again, we would arrive here — so spin inside the granted
     * region and let the harness fail on the timeout rather than on a clean
     * exit that looked like success. */
1:
    j       1b
