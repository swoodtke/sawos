/* SOS boot shim — ESP32-C3 board profile (designs 20, 23), M-mode.
 *
 * THE BOARD HALF ONLY, SINCE DESIGN 23. Design 20 built this file as a sibling
 * copy of `hal/riscv32/kernel/boot.S`, under a rule that named the dedup as a
 * later pass; the pass has run. The trap entry, the 32-word frame save and
 * restore, and `sos_resume_frame` are riscv32 rather than board, so they live
 * once in `../../riscv32-common/kernel/trap.S` — those were exactly the parts
 * the copy said were "that file's, unchanged", and there is nothing left here
 * to keep in step.
 *
 * WHAT REMAINS IS THE THREE BOARD DIFFERENCES design 20 found, and each is a
 * thing a board genuinely owns rather than an accident of the copy:
 *   1. `_start` copies .data from its flash load address (XIP: .text/.rodata
 *      execute in place at 0x4200_0000, only .data moves — see esp32c3.ld).
 *      The mask ROM `jalr`s to 0x4200_0008 after matching the direct-boot
 *      magic, and esp32c3.ld places `.text.boot` (this file) exactly there. The
 *      shim sets up the stack, installs `trap_entry`, copies .data, zeroes
 *      .bss, and calls the Saw `kmain`.
 *   2. `kernel_fault` prints on the Espressif UART and halts. This part has no
 *      sifive_test finisher, so there is no way to make the emulator exit
 *      non-zero; the fault is REPORTED instead, and the harness fails the case
 *      on the transcript. Never silent, which is the discipline that mattered.
 *   3. The kernel stack lives in SRAM, like the rest of .bss.
 *
 * `trap.S` states the contract between the two halves; the short version is
 * that it takes `_stack_top` and `kernel_fault` from here and gives back
 * `trap_entry` and `sos_resume_frame`.
 *
 * This file, `trap.S`, and the CSR/PMP helpers in the shared `sink.c` ARE the
 * riscv32 half of the HAL; nothing above the `ktrap` call is
 * architecture-neutral.
 */

    .equ UART0_FIFO,    0x60000000  /* Espressif UART0 TX/RX FIFO (not 16550) */

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

/* ---- the kernel-mode trap reporter --------------------------------------- */

/* A trap the KERNEL took. The virt profile writes the SiFive test finisher so
 * the emulator exits non-zero carrying mcause; THIS PART HAS NO FINISHER, and
 * Espressif QEMU offers no shutdown door a guest can reach. So the fault is
 * REPORTED on the console and the machine halts: the harness fails the case on
 * the line, and its timeout reaps the emulator. Assembly, because a kernel
 * fault must say so even when the Saw side is what broke — and inline rather
 * than factored into helpers, because `ra` is not safe to spend here.
 *
 * `trap.S` jumps here when the mode witness says the trap came from the kernel
 * itself. It is BOARD code — a device write — which is why the shared half
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

/* 64 KiB kernel stack, reserved in .bss (zeroed by the loop above). `trap.S`
 * loads `_stack_top` to run the Saw handler on it. */
    .section .bss.stack, "aw", @nobits
    .balign 16
    .globl _stack_top
_stack_bottom:
    .skip 0x10000
_stack_top:
