/* SOS boot shim: ESP32-C3 board profile, M-mode.
 *
 * This file is the board half only. The trap entry, the 32-word frame save and
 * restore, and `sos_resume_frame` are shared by every riscv32 board and live
 * in `../../riscv32-common/kernel/trap.S`. What this board owns:
 *   1. `_start` copies .data from its flash load address (XIP: .text/.rodata
 *      execute in place at 0x4200_0000, only .data moves; see esp32c3.ld).
 *      The mask ROM `jalr`s to 0x4200_0008 after matching the direct-boot
 *      magic, and esp32c3.ld places `.text.boot` (this file) exactly there. The
 *      shim sets up the stack, installs `trap_entry`, copies .data, zeroes
 *      .bss, and calls the Saw `kmain`.
 *   2. `kernel_fault` prints on the Espressif UART and halts. This part has no
 *      sifive_test finisher, so the emulator cannot be made to exit non-zero;
 *      the fault is reported instead, and the harness fails the case on the
 *      transcript.
 *   3. The kernel stack lives in SRAM, like the rest of .bss.
 *
 * `trap.S` states the contract between the two halves: it takes
 * `kernel_fault` from here and `_stack_top` from `esp32c3.ld`, and gives back
 * `trap_entry` and `sos_resume_frame`.
 *
 * This file, `trap.S`, and the CSR/PMP helpers in the shared `sink.c` are the
 * riscv32 half of the HAL; `ktrap` and everything above it is
 * architecture-neutral.
 */

    .equ UART0_FIFO,    0x60000000  /* Espressif UART0 TX/RX FIFO (not 16550) */

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* Stack: the reservation in .bss made by esp32c3.ld, top-aligned. */
    la      sp, _stack_top

    /* Interrupts are masked for as long as the kernel runs. The kernel is
     * entered with mstatus.MIE set (ABI.md §7d has the probe), and it has no
     * handler for an interrupt taken in kernel mode, so the first thing it
     * does is clear the bit. Nothing sets it again: the only `mret` that
     * could (`sos_resume_frame`) leaves MPIE clear, and a trap entry clears
     * MIE. */
    csrci   mstatus, 8              /* MSTATUS_MIE */

    /* We are the kernel: mode witness clear. */
    csrw    mscratch, zero

    /* Install the machine trap vector (direct mode: mtvec low bits = 0). */
    la      t0, trap_entry
    csrw    mtvec, t0

    /* XIP: copy .data from its flash load address to SRAM, a word at a time.
     * .text and .rodata are not copied: they execute and are read in place in
     * the flash window (esp32c3.ld has the memory budget). This must run
     * before anything reads a mutable global. */
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
     * No preemption point: this is native code running before kmain, before
     * the interrupt controller and before the trap vector has anything to
     * deliver to. */
    la      t0, _bss_start
    la      t1, _bss_end
3:
    bgeu    t0, t1, 4f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       3b
4:
    /* The copy wrote no instructions, but .data landed in SRAM through the
     * data path and the C3 has a unified cache in front of the flash window;
     * fence before the first fetch of anything that depends on it. */
    fence.i

    /* Enter the Saw kernel. It does not return: `exit_pass` / `exit_fail`
     * print their verdict and halt (there is no finisher here). If it ever
     * does, hang rather than run past the image. */
    call    kmain
hang:
    wfi
    j       hang

/* ---- the kernel-mode trap reporter --------------------------------------- */

/* A trap the kernel took. The virt profile writes the SiFive test finisher so
 * the emulator exits non-zero carrying mcause; this part has no finisher, and
 * Espressif QEMU offers no shutdown door a guest can reach. So the fault is
 * reported on the console and the machine halts: the harness fails the case on
 * the line, and its timeout reaps the emulator. It is assembly so a kernel
 * fault is reported even when the Saw side is what broke, and inline rather
 * than factored into helpers because `ra` is not safe to spend here.
 *
 * `trap.S` jumps here when the mode witness says the trap came from the kernel
 * itself. It is board code (a device write), which is why the shared half
 * takes it as a symbol rather than containing it. */
    .section .text.fault, "ax", @progbits
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

/* ---- reservations -------------------------------------------------------- */

/* The kernel stack is not reserved here: `esp32c3.ld` reserves it inside
 * `.bss` under `KERNEL_STACK_SIZE`, and `_stack_top` is a linker symbol. */
