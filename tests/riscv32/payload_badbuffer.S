/* SOS U-mode payload: a wait buffer the kernel may not write.
 *
 * `Waiter.Wait` answers by COPYING a record into memory the caller supplies —
 * SOS's first kernel-writes-userspace path — so the kernel checks the
 * destination before it writes a byte, and a destination outside the process's
 * writable memory TERMINATES the process. It is caller-checkable in the
 * strongest sense: a process linked its own image and knows where its data is.
 *
 * THIS PAYLOAD HAS NO WRITABLE MEMORY AT ALL. `sos/tests/umode.saw` grants it
 * exactly its own bytes, read+execute, so its writable window is empty and
 * EVERY address is outside it — which is the honest answer for a process with
 * nowhere to receive a record, and what makes this case need no particular
 * address. Zero is used because zero is never in anybody's grant.
 *
 * The misaligned half of the same check is `payload_misalignbuf.S`; the two
 * cover the funnel's two rejection rows.
 *
 * WHY A PAYLOAD AND NOT A ROOT SERVER, the reason `payload_badhandle.S` gives:
 * `Waiter.wait` in the typed layer supplies its OWN buffer out of its frame and
 * never lets a caller name one, so "wait into memory I do not own" is not a
 * thing a Saw process can spell. The kernel's check is still worth testing, and
 * this is the altitude where a raw address legitimately lives.
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ PROCESS_HANDLE,    2
    .equ OP_WAITER_CREATE,  5       /* ProcessOp.WaiterCreate */
    .equ OP_WAITER_WAIT,    2       /* WaiterOp.Wait */
    .equ BAD_BUF,           0       /* word-aligned, and in nobody's grant */
    .equ BUF_CAP,           64      /* ample — the capacity is not what fails */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    /* 1. a Waiter of our own. The handle comes back in the value register. */
    li      a7, OP_WAITER_CREATE
    li      a0, PROCESS_HANDLE
    li      a1, 0
    li      a2, 0
    li      a3, 0
    ecall

    /* 2. wait into memory we do not own. This does not return. */
    mv      a0, a1                  /* the Waiter handle just minted */
    li      a7, OP_WAITER_WAIT
    li      a1, BAD_BUF
    li      a2, BUF_CAP
    li      a3, 0
    ecall

    /* Unreachable. If the kernel ever wrote through that pointer, spin inside
     * the granted region so the harness fails on the timeout rather than on a
     * clean exit that looked like success. */
1:
    j       1b
