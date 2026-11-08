/* EL0 payload: attaching something that is not a waitable.
 *
 * A caller error the process could have checked terminates it. Attaching a
 * handle to a Waiter is that class (the process chose the handle and knows
 * what it names), so the kernel reports `not a waitable`, tears down
 * everything the process owned (the Waiter included, which this case also
 * proves) and stays up.
 *
 * It is a payload, not a Saw root server, for the same reason as
 * `payload_badhandle.S`: `Waiter.add` takes an `&Event` and there is no way to
 * build one from a word.
 *
 * It names two op numbers, the price of that altitude: op numbers are
 * kernel-internal (§5.7's vDSO discipline) and a payload has no `sos` module
 * to call. A renumbering in `kernel/abi/` must edit this file too. Kept in
 * step with the riscv32 twin.
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ SYSTEM_HANDLE,     1       /* not a waitable — the point of the case */
    .equ PROCESS_HANDLE,    2
    .equ OP_WAITER_CREATE,  5       /* ProcessOp.WaiterCreate */
    .equ OP_WAITER_ADD,     0       /* WaiterOp.Add */
    .equ ATTACH_KEY,        7       /* any word; never read, the attach fails */

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

    /* 2. attach the System object to it. This does not return. */
    mov     x0, x1                  /* the Waiter handle just minted */
    mov     x8, #OP_WAITER_ADD
    mov     x1, #SYSTEM_HANDLE
    mov     x2, #ATTACH_KEY
    svc     #0

    /* Unreachable. Spin rather than exit cleanly, so a regression shows up as
     * a timeout and not as a pass. */
1:
    b       1b
