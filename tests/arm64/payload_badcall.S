/* User-mode payload: a caller's mistake is a fault.
 *
 * A caller error the process could have checked (an invalid handle, an op the
 * object does not have, a right it was not given) terminates the process.
 * Statuses are reserved for what the caller could not have known (exhaustion,
 * a dead peer). So `BadHandle`, `BadOp` and `AccessDenied` are `FaultReason`
 * tags in the kernel's report, not statuses.
 *
 * The case proves two things in order, which the harness asserts:
 *
 *   1. a good call still works             -> the '!' reaches the console
 *   2. an unknown op on that same handle   -> the kernel reports `bad op`,
 *                                             tears the process down, and stops
 *                                             the machine with ExitCode
 *                                             .ProcessFault
 *
 * The kernel stays up: it diagnoses the faulting process and reports the
 * teardown rather than faulting itself or hanging.
 *
 * Kept in step with the riscv32 twin. The bad-handle half is covered by
 * `umode_bad_handle`.
 */

    .equ SYSTEM_HANDLE,     1
    .equ OP_DEBUG_PRINT,    0
    .equ OP_UNASSIGNED,     99

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    /* 1. a good call: '!' on the console proves the round trip resumed. */
    mov     x8, #OP_DEBUG_PRINT
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #33                 /* '!' */
    svc     #0

    /* 2. an op the System object does not have. This does not return. */
    mov     x8, #OP_UNASSIGNED
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #0
    svc     #0

    /* Unreachable. If the kernel ever let this run, spin inside the granted
     * region rather than run off into memory EL0 cannot read. */
1:
    b       1b
