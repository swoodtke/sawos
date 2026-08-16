/* SOS EL0 payload: a wait buffer that is not word-aligned.
 *
 * The other rejection row of the copy-out funnel (`payload_badbuffer.S` is the
 * range row). The kernel writes MACHINE WORDS into the record, so a misaligned
 * destination traps on one profile and is merely wrong on the other.
 *
 * ALIGNMENT IS CHECKED FIRST, which is what makes this case distinguishable
 * from the range one: this payload owns no writable memory either, so both
 * checks would refuse the address and the transcript reports whichever runs
 * first. Kept in step with the riscv32 twin.
 *
 * The boot handle set is three wide and in a fixed order (§12): 1 = System,
 * 2 = Process, 3 = the initial Thread.
 */

    .equ PROCESS_HANDLE,    2
    .equ OP_CREATE_WAITER,  5       /* ProcessOp.CreateWaiter */
    .equ OP_WAITER_WAIT,    2       /* WaiterOp.Wait */
    .equ ODD_BUF,           1       /* not a multiple of any word size */
    .equ BUF_CAP,           64

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    mov     x8, #OP_CREATE_WAITER
    mov     x0, #PROCESS_HANDLE
    mov     x1, #0
    mov     x2, #0
    mov     x3, #0
    svc     #0

    mov     x0, x1                  /* the Waiter handle just minted */
    mov     x8, #OP_WAITER_WAIT
    mov     x1, #ODD_BUF
    mov     x2, #BUF_CAP
    mov     x3, #0
    svc     #0

1:
    b       1b
