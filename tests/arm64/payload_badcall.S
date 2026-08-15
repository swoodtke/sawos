/* SOS user-mode payload: a caller's mistake is a FAULT (design 178's faults
 * ruling, ratified Aug 15 — this file used to assert the opposite).
 *
 * THE RULING: a caller error the process could have checked — an invalid handle,
 * an op the object does not have, a right it was not given — TERMINATES the
 * process. Statuses are reserved for what the caller could not have known
 * (exhaustion, and later a dead peer). So `SosStatus.BadHandle`, `.BadOp` and
 * `.AccessDenied` no longer exist as statuses at all: they are `FaultReason`
 * tags in the kernel's own report.
 *
 * What that means here. The M1b version of this payload made three calls and
 * encoded the three statuses it got back into its exit code; under the ruling
 * the second call never returns, so there is nothing after it to encode. The
 * case now proves two things in ORDER, which is what the harness asserts:
 *
 *   1. a good call still works             -> the '!' reaches the console
 *   2. an unknown op on that same handle   -> the kernel reports `bad op`,
 *                                             tears the process down, and stops
 *                                             the machine with ExitCode
 *                                             .ProcessFault
 *
 * THE KERNEL STAYS UP, which is the other half of the ruling: it is the
 * PROCESS that was wrong, so the kernel diagnoses it and reports the teardown
 * rather than faulting itself or hanging.
 *
 * Kept in step with the riscv32 twin. The bad-HANDLE half of the ruling is
 * covered by `thread_fault`, whose root joins a handle it does not hold.
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

    /* Unreachable under the ruling. If the kernel ever let this run again, spin
     * inside the granted region rather than run off into memory EL0 cannot
     * read — the harness reads the exit status, and a hang is a failure. */
1:
    b       1b
