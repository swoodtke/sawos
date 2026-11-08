/* User-mode payload: the happy path.
 *
 * Hand-written EL0 code that touches no memory, so the kernel grants it one
 * region (its own bytes, RX) and nothing else. Every character reaches the
 * console through a System op; the payload has no UART mapping. Expected
 * output: `SOS-U`, then a clean shutdown.
 *
 * It rides the `.payload` section, the same appended-blob slot the root sosimg
 * uses, so it executes in place with no loader involved. On this profile that
 * slot is page-aligned at both ends, because protection granularity is the
 * page and a payload sharing a page with kernel code would hand EL0 that code.
 *
 * Syscall ABI (spec §5.7): x0 = HANDLE, x8 = OP, args x1-x5, `svc #0`; returns
 * x0 = status, x1 = value. x0 and x1 are clobbered by the return, so each call
 * reloads them; x8 survives.
 *
 * Character codes are decimal with a comment rather than character literals:
 * the two assemblers this tree drives do not agree about `#'S'`.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in x0 */
    .equ OP_DEBUG_PRINT,    0
    .equ OP_SHUTDOWN,       1

    .section .payload, "ax", @progbits
    .balign 4
    .globl _umode_payload
_umode_payload:
    mov     x8, #OP_DEBUG_PRINT
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #83                 /* 'S' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #79                 /* 'O' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #83                 /* 'S' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #45                 /* '-' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #85                 /* 'U' */
    svc     #0
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #10                 /* '\n' */
    svc     #0

    mov     x8, #OP_SHUTDOWN
    mov     x0, #SYSTEM_HANDLE
    mov     x1, #0
    svc     #0

    /* `shutdown` does not return. If it did, spin inside the granted region
     * rather than run off into memory EL0 cannot read. */
1:
    b       1b
