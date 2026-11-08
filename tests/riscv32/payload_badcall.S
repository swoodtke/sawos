/* U-mode payload: a caller's mistake is a fault.
 *
 * A caller error the process could have checked (an invalid handle, an op the
 * object does not have, a right it was not given) terminates the process.
 * Statuses are reserved for what the caller could not have known (exhaustion,
 * a dead peer), so `SosStatus.BadHandle`, `.BadOp` and `.AccessDenied` do not
 * exist as statuses: they are `FaultReason` tags in the kernel's own report.
 *
 * The case proves two things in order, which the harness asserts:
 *
 *   1. a good call still works             -> the '!' reaches the console
 *   2. an unknown op on that same handle   -> the kernel reports `bad op`,
 *                                             tears the process down, and stops
 *                                             the machine with ExitCode
 *                                             .ProcessFault
 *
 * The kernel stays up: the process was wrong, so the kernel diagnoses it and
 * reports the teardown rather than faulting itself or hanging.
 *
 * The bad-handle half is covered by `payload_badhandle.S`.
 */

    .equ SYSTEM_HANDLE,     1
    .equ OP_DEBUG_PRINT,    0
    .equ OP_UNASSIGNED,     99

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    /* 1. a good call: '!' on the console proves the round trip resumed. */
    li      a7, OP_DEBUG_PRINT
    li      a0, SYSTEM_HANDLE
    li      a1, '!'
    ecall

    /* 2. an op the System object does not have. This does not return. */
    li      a7, OP_UNASSIGNED
    li      a0, SYSTEM_HANDLE
    li      a1, 0
    ecall

    /* Unreachable. If the kernel ever let this run again, spin
     * inside the granted region rather than run off into memory U-mode cannot
     * read — the harness reads the exit status, and a hang is a failure. */
1:
    j       1b
