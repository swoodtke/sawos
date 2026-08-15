/* SOS M2 unit-1 EL0 payload: spin, then stop (design 178).
 *
 * The interrupt cases need user mode to be RUNNING for a while — an interrupt
 * that arrives while the machine is in the kernel proves nothing about where
 * interrupts are taken — so this payload burns a bounded register loop and then
 * shuts the machine down through a System op.
 *
 * It touches NO memory, which is also why the count is built with two move
 * instructions rather than the assembler's `ldr =` form: that would put the
 * constant in a literal pool and load it, and this payload's whole point is
 * that one grant of its own bytes is the entire address space it needs.
 *
 * THE ITERATION COUNT BUYS TIME, and how much depends on the machine. What the
 * cases need is two milliseconds of user mode (two ticks at the 1 ms period);
 * what this count must not do is outlast the harness's QEMU timeout. Measured
 * here: ~500 million iterations per second, so a hundred million of them is
 * about a fifth of a second and roughly 100x the two milliseconds needed.
 *
 * That rate is three thousand times Profile A's for the same loop, and the
 * difference is not the architecture: a grant there is four-byte-granular,
 * finer than the page an emulator's address translation caches, while a grant
 * here IS pages. See that payload's own note.
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
