/* The frames `child.c` needs that no compiled code makes, in assembly because
 * they choose a call size or move a stack pointer by hand.
 *
 * void child_chain(u32 depth): `depth` call8 frames, then `child_bottom`.
 * void wrap_chain(u32 depth): `depth` call8 frames; deep enough, it spills
 * every older frame on the way down.
 *
 * void spill_viaN(u32 bad): calls `spill_body` with callN. `spill_body`
 * makes `bad` its stack pointer and wraps the register file, so the oldest
 * frames are spilled in turn until this one is: its base save area is the 16
 * bytes below its callee's stack pointer, below `bad`, and the frame's call
 * size, N, picks the overflow handler that faults.
 *
 * void fill_viaN(u32 bad): calls `fill_body` with callN. `fill_body` wraps
 * the register file, which spills this frame, returns to the top of its own
 * frame, makes `bad` its stack pointer and returns: this frame is filled from
 * below `bad`, by the underflow handler for size N, the size `fill_body`'s
 * return address carries.
 *
 * None returns when the access faults. */

    .section .text.child_chain, "ax"
    .align  4
    .global child_chain
    .type   child_chain, @function
child_chain:
    entry   a1, 32
    beqz    a2, 1f
    addi    a10, a2, -1
    call8   child_chain
    retw
1:  call8   child_bottom
    retw

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

    .section .text.spill_body, "ax"
    .align  4
    .type   spill_body, @function
spill_body:
    entry   a1, 32
    mov     a1, a2
    movi    a10, 12
    call8   wrap_chain
    retw

    .section .text.fill_body, "ax"
    .align  4
    .type   fill_body, @function
fill_body:
    entry   a1, 32
    mov     a3, a2
    movi    a10, 12
    call8   wrap_chain
    mov     a1, a3
    retw

    .macro  VIA name, body, frame, call, arg
    .section .text.\name, "ax"
    .align  4
    .global \name
    .type   \name, @function
\name:
    entry   a1, \frame
    mov     \arg, a2
    \call   \body
    retw
    .endm

    VIA spill_via4, spill_body, 32, call4, a6
    VIA spill_via8, spill_body, 32, call8, a10
    VIA spill_via12, spill_body, 48, call12, a14
    VIA fill_via4, fill_body, 32, call4, a6
    VIA fill_via8, fill_body, 32, call8, a10
    VIA fill_via12, fill_body, 48, call12, a14
