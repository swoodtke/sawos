/* SOS boot shim — QEMU `virt` board profile (designs 112, 140, 23), M-mode.
 *
 * THE BOARD HALF ONLY, SINCE DESIGN 23. The trap entry, the 32-word frame save
 * and restore, and `sos_resume_frame` are riscv32 rather than board, so they
 * live once in `../../riscv32-common/kernel/trap.S` and are shared with every
 * riscv32 board. What is left here is the three things a board genuinely owns:
 *
 *   1. `_start` — a reset entry is whatever this board's loader jumps to.
 *      QEMU (`-M virt -bios none -kernel sos.elf`) resets into M-mode and jumps
 *      to the ELF entry at RAM base 0x8000_0000, where `.text.boot` (this file)
 *      is placed first by virt.ld. The shim sets up the stack, installs
 *      `trap_entry`, zeroes .bss, and calls the Saw `kmain`.
 *   2. `kernel_fault` — reporting a kernel-mode trap means writing a DEVICE,
 *      and this board has a sifive_test finisher to fail through.
 *   3. The kernel STACK reservation, which is a memory budget.
 *
 * `trap.S` states the contract between the two halves; the short version is
 * that it takes `_stack_top` and `kernel_fault` from here and gives back
 * `trap_entry` and `sos_resume_frame`.
 *
 * This file, `trap.S`, and the CSR/PMP helpers in the shared `sink.c` ARE the
 * riscv32 half of the HAL; nothing above the `ktrap` call is
 * architecture-neutral.
 */

    .equ SIFIVE_TEST,   0x00100000
    .equ FINISHER_FAIL, 0x3333

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

    /* Zero .bss [_bss_start, _bss_end) a word at a time.
     *
     * AUDIT (design 1): no preemption point. This is native code running before
     * kmain, before the interrupt controller and before the trap vector has
     * anything to deliver to — pre-IRQ by construction. */
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

/* ---- the kernel-mode trap reporter --------------------------------------- */

/* A trap the KERNEL took: write FINISHER_FAIL with mcause in the code bits, so
 * the emulator exits non-zero carrying the fault cause.
 *
 * `trap.S` jumps here when the mode witness says the trap came from the kernel
 * itself. It is BOARD code — a device write — which is why the shared half
 * takes it as a symbol rather than containing it. */
    .section .text.fault, "ax", @progbits
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

/* ---- reservations -------------------------------------------------------- */

/* 64 KiB kernel stack, reserved in .bss (zeroed by the loop above). `trap.S`
 * loads `_stack_top` to run the Saw handler on it. */
    .section .bss.stack, "aw", @nobits
    .balign 16
    .globl _stack_top
_stack_bottom:
    .skip 0x10000
_stack_top:
