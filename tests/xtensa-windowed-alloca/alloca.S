/* The frames `alloca.c` needs that no compiled code makes: a caller of each
 * call size, and a callee that moves its stack pointer by hand.
 *
 * void wrap_chain(u32 depth): `depth` call8 frames, each calling the next;
 * deep enough, it spills every older frame on the way down.
 *
 * u32 hold4(u32 seed), hold8, hold12: a frame that holds seed + i in each
 * register a3..a(N-1) it owns, calls `mover` and then `mover_return` with
 * callN, and checks each register against a2 afterwards. It answers a2,
 * which the caller compares with `seed`, or 0x80000000 | i for the first
 * register i that changed. */

    .section .text.wrap_chain, "ax"
    .align  4
    .global wrap_chain
    .type   wrap_chain, @function
wrap_chain:
    entry   a1, 32
    beqz    a2, 1f
    addi    a10, a2, -1
    call8   wrap_chain
1:  retw

    /* A frame of `n` registers, a0..a(n-1); `s` and `t` are the callee's
     * first two registers, scratch once it has returned. */
    .macro  HOLD name, n, frame, call, s, t
    .section .text.\name, "ax"
    .align  4
    .global \name
    .type   \name, @function
\name:
    entry   a1, \frame
    .irp    i, 3,4,5,6,7,8,9,10,11
    .if     \i < \n
    addi    a\i, a2, \i
    .endif
    .endr
    \call   mover
    \call   mover_return
    .irp    i, 3,4,5,6,7,8,9,10,11
    .if     \i < \n
    addi    \s, a2, \i
    movi    \t, \i
    bne     a\i, \s, .L\name\()_bad
    .endif
    .endr
    retw
.L\name\()_bad:
    movi    \s, 1
    slli    \s, \s, 31
    or      a2, \s, \t
    retw
    .endm

    HOLD hold4, 4, 32, call4, a4, a5
    HOLD hold8, 8, 32, call8, a8, a9
    HOLD hold12, 12, 48, call12, a12, a13

/* `mover` spills every older frame, its caller's included, moves its stack
 * pointer 64 bytes down with `movsp` (the caller is spilled, so that is the
 * Alloca exception, which fills the caller back), then wraps the register
 * file again below the new stack pointer. `mover_return` does the same but
 * returns straight after the `movsp`, so the caller's frame is found as the
 * Alloca path left it: a fill of the wrong size leaves a WINDOWSTART bit
 * that the second wrap would spill away and the return does not. */
    .section .text.mover, "ax"
    .align  4
    .type   mover, @function
mover:
    entry   a1, 32
    movi    a10, 20
    call8   wrap_chain
    addi    a8, a1, -64
    movsp   a1, a8
    movi    a10, 20
    call8   wrap_chain
    retw

    .section .text.mover_return, "ax"
    .align  4
    .type   mover_return, @function
mover_return:
    entry   a1, 32
    movi    a10, 20
    call8   wrap_chain
    addi    a8, a1, -64
    movsp   a1, a8
    retw
