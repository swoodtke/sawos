/* SOS EL0 payload: attaching something that is not a waitable.
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
 * thing a Saw process can spell. That is the typed layer's guarantee working.
 *
 * IT NAMES TWO OP NUMBERS, which is the price of that altitude: op numbers are
 * kernel-INTERNAL (§5.7's vDSO discipline) and a Saw process never writes one,
 * but a payload has no `sos` module to call. A renumbering in
 * `sos/kernel/abi/` must edit this file too. Kept in step with the riscv32
 * twin.
 *
 * The boot handle set is three wide and in a fixed order (§12, minted by
 * `start_process`): 1 = System, 2 = Process, 3 = the initial Thread.
 */

    .equ SYSTEM_HANDLE,     1       /* not a waitable — the point of the case */
    .equ PROCESS_HANDLE,    2
    .equ OP_CREATE_WAITER,  5       /* ProcessOp.CreateWaiter */
    .equ OP_WAITER_ADD,     0       /* WaiterOp.Add */
    .equ ATTACH_KEY,        7       /* any word; never read, the attach fails */

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

    /* 2. attach the SYSTEM object to it. This does not return. */
    mov     x0, x1                  /* the Waiter handle just minted */
    mov     x8, #OP_WAITER_ADD
    mov     x1, #SYSTEM_HANDLE
    mov     x2, #ATTACH_KEY
    svc     #0

    /* Unreachable under the ruling — see the riscv32 twin. Spin rather than
     * exit cleanly, so a regression shows up as a timeout and not as a pass. */
1:
    b       1b
