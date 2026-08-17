/* SOS U-mode payload: attaching something that is not a waitable.
 *
 * Design 178's faults ruling: a caller error the process could have CHECKED
 * terminates it. Attaching a handle to a Waiter is exactly that class — the
 * process chose the handle and knows what it names — so the kernel reports
 * `not a waitable`, tears down everything the process owned (the Waiter
 * included, which is the other half this case proves) and stays up.
 *
 * WHY THIS IS A PAYLOAD AND NOT A ROOT SERVER WRITTEN IN SAW, the same reason
 * `payload_badhandle.S` is: `Waiter.add` takes an `&Event` and there is no way
 * to build one from a word, so "attach the System object to a waiter" is not a
 * thing a Saw process can spell. That is the typed layer's guarantee working,
 * and it puts the test of the KERNEL's validation at the altitude where raw
 * handles legitimately live.
 *
 * IT NAMES TWO OP NUMBERS, which is the price of that altitude and is worth
 * stating plainly: op numbers are kernel-INTERNAL (§5.7's vDSO discipline) and
 * a Saw process never writes one. A payload has no `sos` module to call, so it
 * writes them, and a renumbering in `sos/kernel/abi/` must edit this file too.
 * The alternative — no coverage of the kernel's own check — is worse.
 *
 * The boot handle set is three wide and in a fixed order (§12, minted by
 * `start_process`): 1 = System, 2 = Process, 3 = the initial Thread.
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
    li      a7, OP_WAITER_CREATE
    li      a0, PROCESS_HANDLE
    li      a1, 0
    li      a2, 0
    li      a3, 0
    ecall

    /* 2. attach the SYSTEM object to it. This does not return. */
    mv      a0, a1                  /* the Waiter handle just minted */
    li      a7, OP_WAITER_ADD
    li      a1, SYSTEM_HANDLE
    li      a2, ATTACH_KEY
    ecall

    /* Unreachable under the ruling. If the kernel ever answered this with a
     * status again, spin inside the granted region so the harness fails on the
     * timeout rather than on a clean exit that looked like success. */
1:
    j       1b
