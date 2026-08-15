/* SOS M2 unit-1 U-mode payload: spin, then stop (design 178).
 *
 * The interrupt cases need user mode to be RUNNING for a while — an interrupt
 * that arrives while the machine is in the kernel proves nothing about where
 * interrupts are taken — so this payload burns a bounded register loop and then
 * shuts the machine down through a System op.
 *
 * It touches NO memory: the loop counter is a register and the syscall carries
 * its argument in one, so the kernel can grant it its own bytes (RX) and
 * nothing else, exactly as `payload_ok.S` is granted. A stack would be a second
 * grant with nothing to hold.
 *
 * THE ITERATION COUNT BUYS TIME, and how much depends on the machine. What the
 * cases need is two milliseconds of user mode (two ticks at the 1 ms period);
 * what this count must not do is outlast the harness's QEMU timeout. Measured
 * here: ~0.3 million iterations per second, so 300 000 of them is about a
 * second of wall clock and roughly 500x the two milliseconds needed.
 *
 * WHY THAT RATE IS SO LOW, because it is startling and it will be measured
 * again: user mode on this profile is granted a region at FOUR-BYTE
 * granularity, which is finer than the page the emulator's address translation
 * caches, so every instruction fetch takes its slow path. The same payload
 * under a page-aligned grant runs the same loop three THOUSAND times faster
 * (measured both ways, design 178 M2 unit 1). It is an emulator artifact, not
 * a property of the hardware — and it is why this count is not Profile B's.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in a0 */
    .equ OP_SHUTDOWN,       1
    .equ SPIN_ITERATIONS,   300000

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      t0, SPIN_ITERATIONS
1:
    addi    t0, t0, -1
    bnez    t0, 1b

    li      a7, OP_SHUTDOWN
    li      a0, SYSTEM_HANDLE
    li      a1, 0
    ecall

    /* `shutdown` does not return. If it ever did, spin inside the granted
     * region rather than run off into memory U-mode cannot read. */
2:
    j       2b
