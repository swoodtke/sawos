/* User-mode payload: spin, then stop, on Xtensa.
 *
 * The interrupt cases need user mode to run for a while (an interrupt that
 * arrives while the machine is in the kernel proves nothing about where
 * interrupts are taken), so this payload burns a bounded register loop and
 * then shuts the machine down through a System op. It touches no memory.
 *
 * The count is the riscv32 payload's, near enough: `movi` takes twelve bits,
 * so it is built as 1525 << 16. It has to cover a couple of timer ticks of
 * user mode and must not outlast the harness's deadline; it clears both by a
 * wide margin.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in a2 */
    .equ OP_SHUTDOWN,       1
    .equ SPIN_HIGH,         1525    /* SPIN_HIGH << 16 iterations */

    .section .payload, "ax"
    .balign 4
    .globl _umode_payload
_umode_payload:
    movi    a4, SPIN_HIGH
    slli    a4, a4, 16
1:
    addi    a4, a4, -1
    bnez    a4, 1b

    movi    a8, OP_SHUTDOWN
    movi    a2, SYSTEM_HANDLE
    movi    a3, 0
    syscall

    /* `shutdown` does not return. If it ever did, spin here rather than run
     * off the end of the payload. */
2:
    j       2b
