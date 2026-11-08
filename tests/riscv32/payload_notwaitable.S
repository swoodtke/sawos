/* U-mode payload: attaching something that is not a waitable.
 *
 * A caller error the process could have checked terminates it. Attaching a
 * handle to a Waiter is that class (the process chose the handle and knows what
 * it names), so the kernel reports `not a waitable`, tears down everything the
 * process owned (the Waiter included, which this case also proves) and stays
 * up.
 *
 * This is a payload and not a root server written in Saw, for the reason
 * `payload_badhandle.S` gives: `Waiter.add` takes an `&Event` and there is no
 * way to build one from a word, so "attach the System object to a waiter"
 * cannot be spelled in Saw.
 *
 * It names two op numbers, the price of that altitude. Op numbers are
 * kernel-internal (§5.7's vDSO discipline) and a Saw process never writes one;
 * a payload has no `sos` module, so it does, and a renumbering in
 * `kernel/abi/` must edit this file too.
 *
 * The boot handle set is three wide and in a fixed order (§12, minted by
 * `start_process`): 1 = System, 2 = Process, 3 = the initial Thread.
 */

    .equ SYSTEM_HANDLE,     1       /* not a waitable; the point of the case */
    .equ PROCESS_HANDLE,    2
    .equ OP_WAITER_CREATE,  5       /* ProcessOp.WaiterCreate */
    .equ OP_WAITER_ADD,     0       /* WaiterOp.Add */
    .equ ATTACH_KEY,        7       /* any word; never read, the attach fails */

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

    /* 2. attach the System object to it. This does not return. */
    mv      a0, a1                  /* the Waiter handle just minted */
    li      a7, OP_WAITER_ADD
    li      a1, SYSTEM_HANDLE
    li      a2, ATTACH_KEY
    ecall

    /* Unreachable. If the kernel ever answered this with a status, spin
     * inside the granted region so the harness fails on the timeout rather than
     * on a clean exit that looks like success. */
1:
    j       1b
