/* SOS boot shim + trap entry — ESP32-C3 board profile (design 20), M-mode.
 *
 * ORIGIN: sibling copy of hal/riscv32/kernel/boot.S (the SIBLING-COPY rule,
 * ../README.md). The trap entry, the frame layout, the resume path and
 * `sos_resume_frame` are that file's, unchanged — they are RISC-V, not board.
 * THREE THINGS DIFFER, all board:
 *   1. `_start` copies .data from its flash load address (XIP: .text/.rodata
 *      execute in place at 0x4200_0000, only .data moves — see esp32c3.ld).
 *   2. `kernel_fault` prints on the Espressif UART and halts. This part has no
 *      sifive_test finisher, so there is no way to make the emulator exit
 *      non-zero; the fault is REPORTED instead, and the harness fails the case
 *      on the transcript. Never silent, which is the discipline that mattered.
 *   3. The kernel stack lives in SRAM, like the rest of .bss.
 * Keep the pair in step until the named shared-core dedup pass merges them.
 *
 * The mask ROM `jalr`s to 0x4200_0008 after matching the direct-boot magic, and
 * esp32c3.ld places `.text.boot` (this file) exactly there. The shim sets up
 * the stack, installs `trap_entry`, copies .data, zeroes .bss, and calls the
 * Saw `kmain`.
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

    .equ UART0_FIFO,    0x60000000  /* Espressif UART0 TX/RX FIFO (not 16550) */
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

    /* XIP: copy .data from its flash load address to SRAM, a word at a time.
     * .text and .rodata are NOT copied — they execute and are read in place in
     * the flash window (design 20 blocker A's ruling; esp32c3.ld holds the
     * arithmetic). This must run before anything reads a mutable global. */
    la      t0, _data_lma
    la      t1, _data_start
    la      t2, _data_end
1:
    bgeu    t1, t2, 2f
    lw      t3, 0(t0)
    sw      t3, 0(t1)
    addi    t0, t0, 4
    addi    t1, t1, 4
    j       1b
2:

    /* Zero .bss [_bss_start, _bss_end) a word at a time.
     *
     * AUDIT (design 1): no preemption point. This is native code running before
     * kmain, before the interrupt controller and before the trap vector has
     * anything to deliver to — pre-IRQ by construction. */
    la      t0, _bss_start
    la      t1, _bss_end
3:
    bgeu    t0, t1, 4f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       3b
4:
    /* The copy wrote instructions nowhere, but .data landed in SRAM through the
     * data path and the C3 has a unified cache in front of the flash window;
     * fence before the first fetch of anything that depends on it. */
    fence.i

    /* Enter the Saw kernel. On this board it does not exit — `exit_pass` /
     * `exit_fail` print their verdict and halt (there is no finisher here) — so
     * a return is as impossible as on virt; hang rather than run past the
     * image. */
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

/* A trap the KERNEL took. The virt profile writes the SiFive test finisher so
 * the emulator exits non-zero carrying mcause; THIS PART HAS NO FINISHER, and
 * Espressif QEMU offers no shutdown door a guest can reach. So the fault is
 * REPORTED on the console and the machine halts: the harness fails the case on
 * the line, and its timeout reaps the emulator. Assembly, because a kernel
 * fault must say so even when the Saw side is what broke — and inline rather
 * than factored into helpers, because `ra` is not safe to spend here. */
    .globl kernel_fault
kernel_fault:
    li      t0, UART0_FIFO
    la      t3, .Lfault_msg
5:
    lbu     t1, 0(t3)
    beqz    t1, 6f
    sw      t1, 0(t0)
    addi    t3, t3, 1
    j       5b
6:
    csrr    t4, mcause
    li      t2, 28
7:
    srl     t1, t4, t2
    andi    t1, t1, 0xf
    li      t5, 10
    blt     t1, t5, 8f
    addi    t1, t1, 39          /* 'a' - '0' - 10 */
8:
    addi    t1, t1, 48          /* '0' */
    sw      t1, 0(t0)
    addi    t2, t2, -4
    bgez    t2, 7b
    li      t1, 10              /* '\n' */
    sw      t1, 0(t0)
9:
    wfi
    j       9b

    .section .rodata.boot, "a", @progbits
.Lfault_msg:
    .asciz "SOS-C3: kernel fault mcause=0x"

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
