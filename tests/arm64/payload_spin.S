/* EL0 payload: spin, then stop.
 *
 * The interrupt cases need user mode to be running for a while: an interrupt
 * that arrives while the machine is in the kernel proves nothing about where
 * interrupts are taken. So this payload burns a bounded register loop and
 * then shuts the machine down through a System op.
 *
 * It touches no memory, which is why the count is built with two move
 * instructions rather than the assembler's `ldr =` form: that would put the
 * constant in a literal pool and load it, and the payload needs nothing
 * beyond one grant of its own bytes.
 *
 * The iteration count buys time. The cases need enough user-mode time for
 * the timer ticks they count; the count must not outlast the harness's QEMU
 * timeout. It is chosen to clear both limits by a wide margin.
 *
 * The riscv32 payload spins the same count at the same order of speed.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in x0 */
    .equ OP_SHUTDOWN,       1
    .equ SPIN_HI,           0x05F5  /* 100000000 = 0x05F5E100 */
    .equ SPIN_LO,           0xE100

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    movz    x9, #SPIN_HI, lsl #16
    movk    x9, #SPIN_LO
1:
    subs    x9, x9, #1
    b.ne    1b

    mov     x8, #OP_SHUTDOWN
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #0
    svc     #0

    /* `shutdown` does not return. If it ever did, spin inside the granted
     * region rather than run off into memory EL0 cannot read. */
2:
    b       2b
