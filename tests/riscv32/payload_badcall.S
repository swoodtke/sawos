/* SOS M1 unit-A U-mode payload: bad ops and bad handles (design 140).
 *
 * A caller's mistake is an ERROR, not a fault: an op the object does not have,
 * or a handle naming nothing, comes back as a status word in a0 and the process
 * keeps running. That is the property under test — the kernel must not turn a
 * bad call into a kill, and must not hang.
 *
 * The payload makes three calls and shuts down with a code that encodes what it
 * saw, so the emulator's exit status IS the assertion:
 *
 *   1. a valid op on a valid handle  -> expect SYS_OK (0)
 *   2. an unknown op on that handle  -> expect SYS_ERR_BAD_OP (2)
 *   3. any op on an unknown handle   -> expect SYS_ERR_BAD_HANDLE (1)
 *
 * Exit code EXPECTED_CODE means all three matched; anything else means one did
 * not, and the code says which.
 */

    .equ SYSTEM_HANDLE,     1
    .equ BOGUS_HANDLE,      42
    .equ OP_DEBUG_PRINT,    0
    .equ OP_SHUTDOWN,       1
    .equ OP_UNASSIGNED,     99

    .equ SYS_OK,            0
    .equ SYS_ERR_BAD_HANDLE, 1
    .equ SYS_ERR_BAD_OP,    2

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
    li      a7, OP_DEBUG_PRINT
    li      a0, SYSTEM_HANDLE
    li      a1, '?'
    ecall
    li      t0, SYS_OK
    bne     a0, t0, .Lfail1

    /* 2. unknown op on a valid handle */
    li      a7, OP_UNASSIGNED
    li      a0, SYSTEM_HANDLE
    li      a1, 0
    ecall
    li      t0, SYS_ERR_BAD_OP
    bne     a0, t0, .Lfail2

    /* 3. valid op on a handle that names nothing */
    li      a7, OP_DEBUG_PRINT
    li      a0, BOGUS_HANDLE
    li      a1, 'X'
    ecall
    li      t0, SYS_ERR_BAD_HANDLE
    bne     a0, t0, .Lfail3

    li      a1, EXPECTED_CODE
    j       .Lshutdown
.Lfail1:
    li      a1, FAIL_STEP1
    j       .Lshutdown
.Lfail2:
    li      a1, FAIL_STEP2
    j       .Lshutdown
.Lfail3:
    li      a1, FAIL_STEP3
.Lshutdown:
    li      a7, OP_SHUTDOWN
    li      a0, SYSTEM_HANDLE
    ecall

1:
    j       1b
