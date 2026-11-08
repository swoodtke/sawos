/* EL0 payload: an op on a handle the process never held.
 *
 * A caller error the process could have checked terminates it; the kernel
 * reports the reason, tears down everything the process owned, and stays up.
 * Expected outcome: process fault.
 *
 * It is a payload, not a Saw root server: the `sos` typed layer cannot build a
 * `Thread` from a word, so "a handle I was never given" cannot be spelled in
 * Saw. This tests the kernel's validation where raw handles legitimately live,
 * beside `payload_badcall.S`, which tests the bad-op half.
 *
 * Dispatch resolves the handle before it decodes the op (spec §3), so an
 * unbound handle is rejected with the op word never looked at. The payload
 * therefore names no op number that could go stale. Kept in step with the
 * riscv32 twin.
 */

    .equ NEVER_HELD_HANDLE, 42      /* the boot set is three wide and made none */
    .equ ANY_OP,            0       /* never decoded — the handle is rejected first */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    mov     x8, #ANY_OP
    mov     x0, #NEVER_HELD_HANDLE
    mov     x1, #0
    svc     #0

    /* Unreachable. Spin rather than exit cleanly, so a regression shows up as
     * a timeout and not as a pass. */
1:
    b       1b
