/* SOS U-mode payload: a wait buffer that is not word-aligned.
 *
 * The other rejection row of the copy-out funnel (`payload_badbuffer.S` is the
 * range row). The kernel writes MACHINE WORDS into the record, so a misaligned
 * destination traps on one profile and is merely wrong on the other — and
 * neither is something to discover at a wake, in another thread's context, long
 * after the call that supplied it.
 *
 * ALIGNMENT IS CHECKED FIRST, which is what makes this case distinguishable
 * from the range one: this payload owns no writable memory either, so both
 * checks would refuse the address and the transcript reports whichever runs
 * first. The order is stated in `copy_out_check` and asserted here.
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ PROCESS_HANDLE,    2
    .equ OP_WAITER_CREATE,  5       /* ProcessOp.WaiterCreate */
    .equ OP_WAITER_WAIT,    2       /* WaiterOp.Wait */
    .equ ODD_BUF,           1       /* not a multiple of any word size */
    .equ BUF_CAP,           64

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    li      a7, OP_WAITER_CREATE
    li      a0, PROCESS_HANDLE
    li      a1, 0
    li      a2, 0
    li      a3, 0
    ecall

    mv      a0, a1                  /* the Waiter handle just minted */
    li      a7, OP_WAITER_WAIT
    li      a1, ODD_BUF
    li      a2, BUF_CAP
    li      a3, 0
    ecall

1:
    j       1b
