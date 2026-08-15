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
 * here: a hundred million iterations put a whole case — emulator startup, boot,
 * loop and shutdown — at 0.06-0.07s, with four ticks reported inside it, so the
 * loop clears the two milliseconds it owes by more than an order of magnitude
 * and clears the timeout by three. It is the SAME count Profile B's payload
 * uses, and that is now meaningful: the two machines run this loop at the same
 * order of speed.
 *
 * THEY DID NOT USED TO. This payload was sized at 300 000 when the unit was
 * written, because user mode here ran at ~0.3 million iterations per second —
 * a thousand times slower than Profile B for the same instructions. The cause
 * was the `.payload` section ending mid-page, which left the PMP grant
 * covering part of a page and put QEMU's softmmu on its slow path for every
 * access (DF-178b). The linker script rounds that end up now, and the
 * measurement above is what came back.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in a0 */
    .equ OP_SHUTDOWN,       1
    .equ SPIN_ITERATIONS,   100000000

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
