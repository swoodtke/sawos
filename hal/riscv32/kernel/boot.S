/* SOS boot shim + trap entry (designs 112, 140) — riscv32, QEMU `virt`, M-mode.
 *
 * QEMU (`-M virt -bios none -kernel sos.elf`) resets into M-mode and jumps to
 * the ELF entry at RAM base 0x8000_0000, where `.text.boot` (this file) is
 * placed first by virt.ld. The shim sets up the stack, installs `trap_entry`,
 * zeroes .bss, and calls the Saw `kmain`.
 *
 * M1 adds the M/U split. `mscratch` is the MODE WITNESS: it holds 0 while the
 * kernel runs and the RUNNING THREAD'S FRAME ADDRESS while U-mode runs.
 * `trap_entry` swaps it with sp on entry, so a single `bnez` separates the two
 * worlds:
 *
 *   nonzero -> the trap came from U-mode. sp now points at the trap frame;
 *              save the user context, run the Saw handler `ktrap`, restore,
 *              `mret` back.
 *   zero    -> the trap came from the kernel itself, i.e. a kernel bug. There
 *              is no context worth saving: FAIL the run through the SiFive
 *              finisher exactly as M0's stub did (never hang).
 *
 * Trap frame layout (32 words, 128 bytes): word `i` holds register `x<i>`,
 * and word 0 — the slot x0 would occupy, which never needs saving — holds
 * `mepc`. So a register's offset is just its number times four.
 *
 * DESIGN 178 M2 UNIT 2 — THE FRAME IS A THREAD'S CONTEXT. The kernel owns the
 * storage (one slot per thread, in its own arena) and `ktrap` RETURNS the frame
 * to resume, so this file no longer reserves a frame of its own and the restore
 * path below is the whole of the context switch. Re-arming `mscratch` from the
 * resumed sp is what makes the next trap save into the thread that is actually
 * running.
 *
 * This file plus the CSR/PMP helpers in sink.c ARE the riscv32 half of the HAL;
 * nothing above the `ktrap` call is architecture-neutral.
 */

    .equ SIFIVE_TEST,   0x00100000
    .equ FINISHER_FAIL, 0x3333
    .equ MSTATUS_MPP,   0x1800      /* bits 12:11 — previous privilege mode */
    .equ MSTATUS_MPIE,  0x80        /* bit 7 — previous interrupt enable */

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* Stack: the 64 KiB reservation below (in .bss), top-aligned. */
    la      sp, _stack_top

    /* We are the kernel: mode witness clear. */
    csrw    mscratch, zero

    /* Install the machine trap vector (direct mode: mtvec low bits = 0). */
    la      t0, trap_entry
    csrw    mtvec, t0

    /* Zero .bss [_bss_start, _bss_end) a word at a time. */
    la      t0, _bss_start
    la      t1, _bss_end
1:
    bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:
    /* Enter the Saw kernel. It exits via the SiFive finisher and does not
     * return; if it ever does, hang rather than execute past the image. */
    call    kmain
hang:
    wfi
    j       hang

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
     * FAIL. (M0's discipline, unchanged: a kernel fault never hangs.) */
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
     * the witness — from here on we are the kernel. */
    csrr    t0, mscratch
    sw      t0,   2*4(sp)
    csrw    mscratch, zero

    csrr    t0, mepc
    sw      t0,   0*4(sp)

    /* Run the handler on the KERNEL stack. The frame address goes in a0 and
     * comes BACK in a0: `ktrap` returns the frame to resume, which is either
     * this one or another thread's (design 178 M2 unit 2). Nothing has to be
     * preserved across the call, so no callee-saved register is spent. */
    mv      a0, sp
    csrr    a1, mcause
    csrr    a2, mtval
    la      sp, _stack_top
    call    ktrap

    /* Resume whatever context a0 names. `ktrap` may have advanced mepc past an
     * `ecall` and written syscall results into a0/a1 OF THAT FRAME — and the
     * frame need not be the one we came in on, which is the whole of the
     * context switch: mscratch below is re-armed with whatever we resume, so
     * the next trap saves into the thread that is actually running. */
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
    lw      x2,   2*4(sp)           /* user sp LAST — it retires the frame ptr */
    mret

/* A trap the KERNEL took: write FINISHER_FAIL with mcause in the code bits, so
 * the emulator exits non-zero carrying the fault cause. */
    .globl kernel_fault
kernel_fault:
    csrr    t0, mcause
    slli    t0, t0, 16
    li      t1, FINISHER_FAIL
    or      t0, t0, t1
    li      t2, SIFIVE_TEST
    sw      t0, 0(t2)
3:
    wfi
    j       3b

/* ---- M -> U transition --------------------------------------------------- */

/* void sos_resume_frame(u32 frame_addr) — never returns.
 *
 * Enter user mode in the context `frame_addr` holds. This is the FIRST entry
 * only: every entry after it is `ktrap` returning a frame to the path above,
 * which is why this is four instructions and a branch INTO that path rather
 * than a second copy of it. It replaced `sos_enter_user`, which built a user
 * context by zeroing twenty-nine registers in assembly — the same context is
 * now built by `frame_init` in Saw, and this only has to select the mode.
 *
 * What is left is the part with no Saw spelling (design 172's reason 1): MPP =
 * 0 selects U-mode as the `mret` target and MPIE = 0 leaves the global
 * interrupt enable clear, which is design 178 D2 — this hart delivers a machine
 * interrupt to a lower privilege mode regardless of that bit, so clearing it
 * IS "interrupts are taken from user mode only".
 *
 * A trap RETURN does not repeat this: the hardware sets MPP to the mode it
 * trapped from, which is U for every trap that reaches the path above. */
    .section .text.enter, "ax", @progbits
    .globl sos_resume_frame
sos_resume_frame:
    li      t1, MSTATUS_MPP
    csrc    mstatus, t1
    li      t1, MSTATUS_MPIE
    csrc    mstatus, t1
    j       .Lresume_frame

/* ---- reservations -------------------------------------------------------- */

/* 64 KiB kernel stack, reserved in .bss (zeroed by the loop above). */
    .section .bss.stack, "aw", @nobits
    .balign 16
    .globl _stack_top
_stack_bottom:
    .skip 0x10000
_stack_top:
