/* SOS riscv32 trap entry and M -> U transition, shared by every riscv32 board.
 *
 * Nothing here is board-specific: the mode witness, the 32-word frame save and
 * restore, and the privilege select an `mret` needs. The frame is described
 * once in Saw (`rv32core`'s `TrapFrame`) and once here, in the stores below.
 *
 * Each board's `boot.S` owns `_start` (the reset entry; on an XIP part it
 * copies `.data` first) and `kernel_fault` (reporting a kernel-mode trap writes
 * a board-specific device); each board's linker script reserves the kernel
 * stack. This file's contract
 * with the board and the Saw side, which assembly cannot declare:
 *
 *   IN:  `_stack_top`   the board's kernel stack, reserved inside `.bss` by
 *                       its linker script
 *        `kernel_fault` the board's kernel-mode-trap reporter, `.globl`
 *        `ktrap`        the Saw handler (kcore): takes a frame address and
 *                       returns the frame to resume
 *   OUT: `trap_entry`      installed in `mtvec` by the board's `_start`
 *        `sos_resume_frame` called from Saw (`rv32core.resume_frame`)
 *
 * `mscratch` is the mode witness: it holds 0 while the kernel runs and the
 * running thread's frame address while U-mode runs. `trap_entry` swaps it with
 * sp on entry, so a single `bnez` separates the two worlds:
 *
 *   nonzero -> the trap came from U-mode. sp now points at the trap frame;
 *              save the user context, run `ktrap`, restore, `mret` back.
 *   zero    -> the trap came from the kernel itself, i.e. a kernel bug. There
 *              is no context worth saving: fail the run through the board's
 *              `kernel_fault` (never hang).
 *
 * Trap frame layout (32 words, 128 bytes): word `i` holds register `x<i>`,
 * and word 0, the slot x0 would occupy and which never needs saving, holds
 * `mepc`. A register's offset is its number times four.
 *
 * The frame is a thread's context. The kernel owns the storage (one slot per
 * thread) and `ktrap` returns the frame to resume, so this file reserves no
 * frame and the restore path is the whole context switch. Re-arming `mscratch`
 * from the resumed sp makes the next trap save into the thread that is
 * actually running.
 *
 * This file, each board's `boot.S`, and the CSR/PMP helpers in `sink.c` are
 * the riscv32 half of the HAL; `ktrap` and everything above it is
 * architecture-neutral.
 */
    .equ MSTATUS_MPP,   0x1800      /* bits 12:11 — previous privilege mode */
    .equ MSTATUS_MPIE,  0x80        /* bit 7 — previous interrupt enable */

/* ---- trap entry ---------------------------------------------------------- */

    .section .text.trap, "ax", @progbits
    .globl trap_entry
    .balign 4
trap_entry:
    /* sp <-> mscratch. sp now holds the mode witness; mscratch holds the sp
     * the trapped context was using. */
    csrrw   sp, mscratch, sp
    bnez    sp, .Lfrom_user

    /* Kernel-mode trap: undo the swap so sp is the kernel stack again, then
     * fail. A kernel fault never hangs. */
    csrrw   sp, mscratch, sp
    j       kernel_fault

.Lfrom_user:
    sw      x1,   1*4(sp)
    sw      x3,   3*4(sp)
    sw      x4,   4*4(sp)
    sw      x5,   5*4(sp)
    sw      x6,   6*4(sp)
    sw      x7,   7*4(sp)
    sw      x8,   8*4(sp)
    sw      x9,   9*4(sp)
    sw      x10, 10*4(sp)
    sw      x11, 11*4(sp)
    sw      x12, 12*4(sp)
    sw      x13, 13*4(sp)
    sw      x14, 14*4(sp)
    sw      x15, 15*4(sp)
    sw      x16, 16*4(sp)
    sw      x17, 17*4(sp)
    sw      x18, 18*4(sp)
    sw      x19, 19*4(sp)
    sw      x20, 20*4(sp)
    sw      x21, 21*4(sp)
    sw      x22, 22*4(sp)
    sw      x23, 23*4(sp)
    sw      x24, 24*4(sp)
    sw      x25, 25*4(sp)
    sw      x26, 26*4(sp)
    sw      x27, 27*4(sp)
    sw      x28, 28*4(sp)
    sw      x29, 29*4(sp)
    sw      x30, 30*4(sp)
    sw      x31, 31*4(sp)

    /* The user sp is parked in mscratch by the entry swap. Save it, then clear
     * the witness: from here on we are the kernel. */
    csrr    t0, mscratch
    sw      t0,   2*4(sp)
    csrw    mscratch, zero

    csrr    t0, mepc
    sw      t0,   0*4(sp)

    /* Run the handler on the kernel stack. The frame address goes in a0 and
     * comes back in a0: `ktrap` returns the frame to resume, which is either
     * this one or another thread's. Nothing has to be preserved across the
     * call, so no callee-saved register is spent. */
    mv      a0, sp
    csrr    a1, mcause
    csrr    a2, mtval
    la      sp, _stack_top
    call    ktrap

    /* Resume whatever context a0 names. `ktrap` may have advanced mepc past an
     * `ecall` and written syscall results into a0/a1 of that frame. The frame
     * need not be the one we came in on; mscratch below is re-armed with
     * whatever we resume. */
.Lresume_frame:
    mv      sp, a0
    lw      t0,   0*4(sp)
    csrw    mepc, t0
    /* Going back to U-mode: re-arm the witness. */
    csrw    mscratch, sp

    lw      x1,   1*4(sp)
    lw      x3,   3*4(sp)
    lw      x4,   4*4(sp)
    lw      x5,   5*4(sp)
    lw      x6,   6*4(sp)
    lw      x7,   7*4(sp)
    lw      x8,   8*4(sp)
    lw      x9,   9*4(sp)
    lw      x10, 10*4(sp)
    lw      x11, 11*4(sp)
    lw      x12, 12*4(sp)
    lw      x13, 13*4(sp)
    lw      x14, 14*4(sp)
    lw      x15, 15*4(sp)
    lw      x16, 16*4(sp)
    lw      x17, 17*4(sp)
    lw      x18, 18*4(sp)
    lw      x19, 19*4(sp)
    lw      x20, 20*4(sp)
    lw      x21, 21*4(sp)
    lw      x22, 22*4(sp)
    lw      x23, 23*4(sp)
    lw      x24, 24*4(sp)
    lw      x25, 25*4(sp)
    lw      x26, 26*4(sp)
    lw      x27, 27*4(sp)
    lw      x28, 28*4(sp)
    lw      x29, 29*4(sp)
    lw      x30, 30*4(sp)
    lw      x31, 31*4(sp)
    lw      x2,   2*4(sp)           /* user sp last: it retires the frame ptr */
    mret

/* ---- M -> U transition --------------------------------------------------- */

/* void sos_resume_frame(u32 frame_addr) — never returns.
 *
 * Enter user mode in the context `frame_addr` holds. This is the first entry
 * only; every later entry is `ktrap` returning a frame to the path above, so
 * this branches into that path rather than copying it. The frame's contents
 * are built by `frame_init` in Saw; this only selects the mode.
 *
 * MPP = 0 selects U-mode as the `mret` target. MPIE = 0 leaves the global
 * interrupt enable clear: this hart delivers a machine interrupt to a lower
 * privilege mode regardless of that bit, so clearing it means interrupts are
 * taken from user mode only.
 *
 * A trap return does not repeat this: the hardware sets MPP to the mode it
 * trapped from, which is U for every trap that reaches the path above. */
    .section .text.enter, "ax", @progbits
    .globl sos_resume_frame
sos_resume_frame:
    li      t1, MSTATUS_MPP
    csrc    mstatus, t1
    li      t1, MSTATUS_MPIE
    csrc    mstatus, t1
    j       .Lresume_frame
