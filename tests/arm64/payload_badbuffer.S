/* EL0 payload: a wait buffer the kernel may not write.
 *
 * `Waiter.Wait` answers by copying a record into memory the caller supplies,
 * so the kernel checks the destination before writing, and a destination
 * outside the process's writable memory terminates the process. Expected
 * outcome: process fault.
 *
 * This payload has no writable memory at all: `tests/umode.saw` grants it
 * exactly its own bytes, read+execute. Zero is never in anybody's grant. The
 * misaligned half is `payload_misalignbuf.S`.
 *
 * It is a payload, not a root server, because `Waiter.wait` in the typed layer
 * supplies its own buffer and never lets a caller name one, so "wait into
 * memory I do not own" cannot be spelled in Saw. Kept in step with the riscv32
 * twin.
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ PROCESS_HANDLE,    2
    .equ OP_WAITER_CREATE,  5       /* ProcessOp.WaiterCreate */
    .equ OP_WAITER_WAIT,    2       /* WaiterOp.Wait */
    .equ BAD_BUF,           0       /* word-aligned, and in nobody's grant */
    .equ BUF_CAP,           256      /* ample; the capacity is not what fails.
                                     * It must cover the record's buffer,
                                     * message body included, or the wait
                                     * faults as BadArg before the check under
                                     * test. */

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    /* 1. a Waiter of our own. The handle comes back in the value register. */
    mov     x8, #OP_WAITER_CREATE
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

    /* Unreachable. Spin rather than exit cleanly, so a regression shows up as
     * a timeout and not as a pass. */
1:
    b       1b
