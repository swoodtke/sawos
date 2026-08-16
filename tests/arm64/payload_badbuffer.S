/* SOS EL0 payload: a wait buffer the kernel may not write.
 *
 * `Waiter.Wait` answers by COPYING a record into memory the caller supplies —
 * SOS's first kernel-writes-userspace path — so the kernel checks the
 * destination before it writes a byte, and a destination outside the process's
 * writable memory TERMINATES the process.
 *
 * THIS PAYLOAD HAS NO WRITABLE MEMORY AT ALL: `sos/tests/umode.saw` grants it
 * exactly its own bytes, read+execute. Zero is used because zero is never in
 * anybody's grant. The misaligned half is `payload_misalignbuf.S`.
 *
 * WHY A PAYLOAD AND NOT A ROOT SERVER: `Waiter.wait` in the typed layer
 * supplies its OWN buffer out of its frame and never lets a caller name one, so
 * "wait into memory I do not own" is not a thing a Saw process can spell. Kept
 * in step with the riscv32 twin, which states the reasoning in full.
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ PROCESS_HANDLE,    2
    .equ OP_CREATE_WAITER,  5       /* ProcessOp.CreateWaiter */
    .equ OP_WAITER_WAIT,    2       /* WaiterOp.Wait */
    .equ BAD_BUF,           0       /* word-aligned, and in nobody's grant */
    .equ BUF_CAP,           64      /* ample — the capacity is not what fails */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    /* 1. a Waiter of our own. The handle comes back in the value register. */
    mov     x8, #OP_CREATE_WAITER
    mov     x0, #PROCESS_HANDLE
    mov     x1, #0
    mov     x2, #0
    mov     x3, #0
    svc     #0

    /* 2. wait into memory we do not own. This does not return. */
    mov     x0, x1                  /* the Waiter handle just minted */
    mov     x8, #OP_WAITER_WAIT
    mov     x1, #BAD_BUF
    mov     x2, #BUF_CAP
    mov     x3, #0
    svc     #0

    /* Unreachable — see the riscv32 twin. Spin rather than exit cleanly, so a
     * regression shows up as a timeout and not as a pass. */
1:
    b       1b
