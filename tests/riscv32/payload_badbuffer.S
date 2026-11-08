/* U-mode payload: a wait buffer the kernel may not write.
 *
 * `Waiter.Wait` answers by copying a record into memory the caller supplies,
 * so the kernel checks the destination before it writes a byte, and a
 * destination outside the process's writable memory terminates the process.
 * That is a fault rather than a status because the caller could have checked:
 * a process linked its own image and knows where its data is.
 *
 * This payload has no writable memory at all: `tests/umode.saw` grants it
 * exactly its own bytes, read+execute, so every address is outside its
 * writable window and the case needs no particular address. Zero is used
 * because it is never in anybody's grant.
 *
 * The misaligned half of the same check is `payload_misalignbuf.S`; the two
 * cover the copy-out check's two rejection rows.
 *
 * This is a payload and not a root server because the typed `Waiter.wait`
 * supplies its own buffer out of its frame and never lets a caller name one,
 * so "wait into memory I do not own" cannot be spelled in Saw (see
 * `payload_badhandle.S`).
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ PROCESS_HANDLE,    2
    .equ OP_WAITER_CREATE,  5       /* ProcessOp.WaiterCreate */
    .equ OP_WAITER_WAIT,    2       /* WaiterOp.Wait */
    .equ BAD_BUF,           0       /* word-aligned, and in nobody's grant */
    .equ BUF_CAP,           256      /* ample; the capacity is not what fails.
                                     * The record's buffer holds a message
                                     * body, so a short cap would fault as
                                     * BadArg before the check under test. */

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
     * clean exit that looks like success. */
1:
    j       1b
