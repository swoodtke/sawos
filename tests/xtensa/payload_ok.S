/* User-mode payload: the happy path, on Xtensa.
 *
 * Hand-written user code, deliberately tiny and free-standing: it touches no
 * memory at all and runs in place in the `.payload` section, with no loader
 * involved. Every character reaches the console through a System op; the
 * payload has no UART access of its own.
 *
 * Syscall ABI (spec §5.7): a2 = handle, a8 = op, args a3-a5, `syscall`;
 * returns a2 = status, a3 = value. Every syscall is an object op; the handle
 * in a2 is the authority. a2 is overwritten by the return, so each call
 * reloads it.
 */

    .equ SYSTEM_HANDLE,     1       /* the boot handle, also arriving in a2 */
    .equ OP_DEBUG_PRINT,    0
    .equ OP_SHUTDOWN,       1

    .section .payload, "ax"
    .balign 4
    .globl _umode_payload
_umode_payload:
    movi    a8, OP_DEBUG_PRINT
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'S'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'O'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'S'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, '-'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, 'U'
    syscall
    movi    a2, SYSTEM_HANDLE
    movi    a3, '\n'
    syscall

    movi    a8, OP_SHUTDOWN
    movi    a2, SYSTEM_HANDLE
    movi    a3, 0
    syscall

    /* `shutdown` does not return. If it ever did, spin here rather than run
     * off the end of the payload. */
1:
    j       1b
