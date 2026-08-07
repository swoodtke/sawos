/* SOS M1b unit-A user-mode payload: bad ops and bad handles (design 162).
 *
 * A caller's mistake is an ERROR, not a fault: an op the object does not have,
 * or a handle naming nothing, comes back as a status word in x0 and the process
 * keeps running. That is the property under test — the kernel must not turn a
 * bad call into a kill, and must not hang.
 *
 * The payload makes three calls and shuts down with a code that encodes what it
 * saw, so the emulator's exit status IS the assertion:
 *
 *   1. a valid op on a valid handle  -> expect SOS_OK (0)
 *   2. an unknown op on that handle  -> expect SOS_BAD_OP (2)
 *   3. any op on an unknown handle   -> expect SOS_BAD_HANDLE (1)
 *
 * Exit code EXPECTED_CODE means all three matched; anything else means one did
 * not, and the code says which. Kept in step with the riscv32 twin and with
 * `PAYLOAD_CHECKS_PASSED` in tools/sos_runner.py.
 */

    .equ SYSTEM_HANDLE,     1
    .equ BOGUS_HANDLE,      42
    .equ OP_DEBUG_PRINT,    0
    .equ OP_SHUTDOWN,       1
    .equ OP_UNASSIGNED,     99

    .equ SOS_OK,            0
    .equ SOS_BAD_HANDLE,    1
    .equ SOS_BAD_OP,        2

    /* Distinct failure codes so a wrong status names its own step. */
    .equ EXPECTED_CODE,     7
    .equ FAIL_STEP1,        11
    .equ FAIL_STEP2,        12
    .equ FAIL_STEP3,        13

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    /* 1. a good call still works after the bad ones are defined */
    mov     x8, #OP_DEBUG_PRINT
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #63                 /* '?' */
    svc     #0
    cmp     x0, #SOS_OK
    b.ne    .Lfail1

    /* 2. unknown op on a valid handle */
    mov     x8, #OP_UNASSIGNED
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #0
    svc     #0
    cmp     x0, #SOS_BAD_OP
    b.ne    .Lfail2

    /* 3. valid op on a handle that names nothing */
    mov     x8, #OP_DEBUG_PRINT
    mov     x0, #BOGUS_HANDLE
    mov     x1, #88                 /* 'X' */
    svc     #0
    cmp     x0, #SOS_BAD_HANDLE
    b.ne    .Lfail3

    mov     x1, #EXPECTED_CODE
    b       .Lshutdown
.Lfail1:
    mov     x1, #FAIL_STEP1
    b       .Lshutdown
.Lfail2:
    mov     x1, #FAIL_STEP2
    b       .Lshutdown
.Lfail3:
    mov     x1, #FAIL_STEP3
.Lshutdown:
    mov     x8, #OP_SHUTDOWN
    mov     x0, #SYSTEM_HANDLE
    svc     #0

1:
    b       1b
