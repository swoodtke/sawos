/* SOS boot shim, QEMU `virt` board profile, M-mode.
 *
 * Board half only. The trap entry, the 32-word frame save and restore, and
 * `sos_resume_frame` are shared by every riscv32 board and live in
 * `../../riscv32-common/kernel/trap.S`. This file owns what a board owns:
 *
 *   1. `_start`, the reset entry. QEMU (`-M virt -bios none -kernel sos.elf`)
 *      resets into M-mode and jumps to the ELF entry at RAM base 0x8000_0000,
 *      where `.text.boot` (this file) is placed first by virt.ld. The shim
 *      sets up the stack, installs `trap_entry`, zeroes .bss, and calls the
 *      Saw `kmain`.
 *   2. `kernel_fault`: reporting a kernel-mode trap writes a device, and this
 *      board has a sifive_test finisher to fail through.
 *
 * The kernel stack is reserved by `virt.ld`. `trap.S` states the contract
 * between the two halves: it takes `kernel_fault` from here and `_stack_top`
 * from `virt.ld`, and gives back `trap_entry` and `sos_resume_frame`.
 *
 * This file, `trap.S`, and the CSR/PMP helpers in the shared `sink.c` are the
 * riscv32 half of the HAL; `ktrap` and everything above it is
 * architecture-neutral.
 */
    .equ SIFIVE_TEST,   0x00100000
    .equ FINISHER_FAIL, 0x3333

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* Stack: the reservation in .bss made by virt.ld, top-aligned. */
    la      sp, _stack_top

    /* We are the kernel: mode witness clear. */
    csrw    mscratch, zero

    /* Install the machine trap vector (direct mode: mtvec low bits = 0). */
    la      t0, trap_entry
    csrw    mtvec, t0

    /* Zero .bss [_bss_start, _bss_end) a word at a time. No preemption point:
     * this runs before kmain and before any interrupt can be delivered. */
    la      t0, _bss_start
    la      t1, _bss_end
1:
    bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:
    /* Enter the Saw kernel. It exits via the SiFive finisher and does not
     * return; if it does, hang rather than execute past the image. */
    call    kmain
hang:
    wfi
    j       hang

/* ---- the kernel-mode trap reporter --------------------------------------- */

/* A trap the kernel took: write FINISHER_FAIL with mcause in the code bits, so
 * the emulator exits non-zero carrying the fault cause.
 *
 * `trap.S` jumps here when the mode witness says the trap came from the kernel
 * itself. It is board code (a device write), so the shared half takes it as a
 * symbol. */
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
