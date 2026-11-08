/* U-mode payload: spin, then stop.
 *
 * The interrupt cases need user mode to be running for a while (an interrupt
 * that arrives while the machine is in the kernel proves nothing about where
 * interrupts are taken), so this payload burns a bounded register loop and then
 * shuts the machine down through a System op.
 *
 * It touches no memory: the loop counter is a register and the syscall carries
 * its argument in one, so the kernel grants it its own bytes (RX) and nothing
 * else, as for `payload_ok.S`.
 *
 * The iteration count buys time. The cases need a couple of timer ticks of
 * user mode, and the count must not outlast the harness's QEMU timeout; the
 * count is chosen to clear both by a wide margin. The arm64 profile's payload
 * uses the same count. That depends on the linker script rounding the end of
 * `.payload` up to a page: a PMP grant that covers part of a page puts QEMU's
 * softmmu on its slow path for every access.
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
