/* Register-holding call chains at each windowed call size, in assembly
 * because no compiled code chooses `call4` or `call12`.
 *
 * u32 chain4(u32 depth, u32 seed), chain8, chain12
 *
 * Each frame of chainN owns a0..a(N-1). It holds depth in a2, seed in a3
 * and, in each a_i with 4 <= i < N, seed + (depth << 8) + i. Above depth 0
 * it calls itself with callN on (depth - 1, seed); at depth 0 it calls
 * `chain_hook(seed)` with callN. After the call it checks that the callee
 * answered seed + ((depth - 1) << 16) (the hook answers 0 for success) and
 * that every a_i still holds its value, and answers seed + (depth << 16).
 * The answer is not symmetric in a2 and a3, so a swap of the two is seen.
 * Seeds keep bit 31 clear. A mismatch answers 0x80000000 | depth << 8 | i,
 * i the register (2 for the callee's answer), and a failure below is passed
 * up unchanged.
 *
 * A depth past 64 / N frames wraps the register file, so the way down
 * overflows and the way back up underflows, at size N. */

    .macro  CHAIN name, n, frame, call, S, T, A, B
    .section .text.\name, "ax"
    .align  4
    .global \name
    .type   \name, @function
\name:
    entry   a1, \frame
    slli    \S, a2, 8
    add     \S, \S, a3
    .if \n >= 8
    addi    a4, \S, 4
    addi    a5, \S, 5
    addi    a6, \S, 6
    addi    a7, \S, 7
    .endif
    .if \n >= 12
    addi    a8, \S, 8
    addi    a9, \S, 9
    addi    a10, \S, 10
    addi    a11, \S, 11
    .endif
    beqz    a2, .L\name\()_leaf
    addi    \A, a2, -1
    mov     \B, a3
    \call   \name
    addi    \S, a2, -1
    slli    \S, \S, 16
    add     \S, \S, a3
    bne     \A, \S, .L\name\()_badret
.L\name\()_check:
    slli    \S, a2, 8
    add     \S, \S, a3
    .if \n >= 8
    .irp    i, 4,5,6,7
    addi    \T, \S, \i
    movi    \A, \i
    bne     a\i, \T, .L\name\()_bad
    .endr
    .endif
    .if \n >= 12
    .irp    i, 8,9,10,11
    addi    \T, \S, \i
    movi    \A, \i
    bne     a\i, \T, .L\name\()_bad
    .endr
    .endif
    slli    a2, a2, 16
    add     a2, a2, a3
    retw
.L\name\()_leaf:
    mov     \A, a3
    \call   chain_hook
    beqz    \A, .L\name\()_check
    mov     a2, \A
    retw
.L\name\()_badret:
    bbsi    \A, 31, .L\name\()_pass
    movi    \A, 2
.L\name\()_bad:
    movi    \T, 1
    slli    \T, \T, 31
    slli    \S, a2, 8
    or      \T, \T, \S
    or      a2, \T, \A
    retw
.L\name\()_pass:
    mov     a2, \A
    retw
    .endm

    CHAIN chain4, 4, 16, call4, a4, a5, a6, a7
    CHAIN chain8, 8, 32, call8, a8, a9, a10, a11
    CHAIN chain12, 12, 48, call12, a12, a13, a14, a15

/* Spins between a call and the callee's `entry`, until the other thread has
 * reached its own: the tick that lets the other run is taken there, with
 * PS.CALLINC set by the call and the window not yet rotated. The caller's
 * a(4n+1) is free until `entry` writes it, and the two arguments are still
 * in the caller's a(4n+2) and a(4n+3).
 *
 * u32 pre_entry8(u32 *mine, u32 *other): reached by call8; publishes *mine,
 * spins on *other, then `entry` and answers mine ^ other.
 * u32 pre_entry12_via(u32 *mine, u32 *other): the same through call12. */
    .section .text.pre_entry8, "ax"
    .align  4
    .global pre_entry8
    .type   pre_entry8, @function
pre_entry8:
    movi    a9, 1
    s32i    a9, a10, 0
1:  memw
    l32i    a9, a11, 0
    beqz    a9, 1b
    entry   a1, 32
    xor     a2, a2, a3
    retw

    .section .text.pre_entry12, "ax"
    .align  4
pre_entry12:
    movi    a13, 1
    s32i    a13, a14, 0
1:  memw
    l32i    a13, a15, 0
    beqz    a13, 1b
    entry   a1, 32
    xor     a2, a2, a3
    retw

    .section .text.pre_entry12_via, "ax"
    .align  4
    .global pre_entry12_via
    .type   pre_entry12_via, @function
pre_entry12_via:
    entry   a1, 48
    mov     a14, a2
    mov     a15, a3
    call12  pre_entry12
    mov     a2, a14
    retw
