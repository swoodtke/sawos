/* SOS M0 boot shim (design 112) — riscv32, QEMU `virt`, M-mode direct entry.
 *
 * QEMU (`-M virt -bios none -kernel sos.elf`) resets into M-mode and jumps to
 * the ELF entry at RAM base 0x8000_0000, where `.text.boot` (this file) is
 * placed first by virt.ld. The shim sets up the stack, installs a trap vector
 * that FAILS the run (never hangs), zeroes .bss, and calls the Saw `kmain`.
 */

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* Stack: the 64 KiB reservation below (in .bss), top-aligned. */
    la      sp, _stack_top

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

/* Trap vector: a fault must FAIL the test run, never hang it. Write the SiFive
 * test finisher with FINISHER_FAIL (0x3333) and mcause in the code bits, so the
 * emulator exits with a non-zero status carrying the fault cause. */
    .section .text.trap, "ax", @progbits
    .globl trap_entry
    .balign 4
trap_entry:
    csrr    t0, mcause
    slli    t0, t0, 16
    li      t1, 0x3333
    or      t0, t0, t1
    li      t2, 0x00100000      /* sifive_test */
    sw      t0, 0(t2)
3:
    wfi
    j       3b

/* 64 KiB kernel stack, reserved in .bss (zeroed by the loop above). */
    .section .bss.stack, "aw", @nobits
    .balign 16
    .globl _stack_top
_stack_bottom:
    .skip 0x10000
_stack_top:
