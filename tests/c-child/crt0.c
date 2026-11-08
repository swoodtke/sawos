/* The C child's crt0: a plain C function, no assembly, the same file on both
 * profiles, because what the kernel hands a process at entry is already the
 * C calling convention.
 *
 *   - The entry is `_start`, named by every user linker script's
 *     `ENTRY(_start)` and carried into the sosimg header.
 *   - The boot handle arrives in the first argument register (a0 / x0), per
 *     both `hal/<arch>/user/ABI.md`, so a C function of one `unsigned long`
 *     parameter receives it with nothing written.
 *   - The stack pointer is already set: `process_start` puts the destination
 *     region's `link_top()` in it (`kernel/core/dispatch.saw`). Every user
 *     linker script stops a stack's length short of that top and the kernel
 *     grants the gap RW separately. Both profiles' region tops are 16-byte
 *     aligned, as AArch64's sp rule requires.
 *   - `.bss` is already zero: a segment's `mem_len` exceeds its `file_len` and
 *     the kernel zero-fills the tail (`kernel/core/loader.saw`). `.data`
 *     needs no copy: the loader places each segment at its link address.
 *
 * What is left is what this file does: call `main` and turn its return value
 * into a `Process.exit`.
 *
 * Falling off `_start` is the documented death, and this file uses it as its
 * error path. The kernel enters a process with a zero link register (spec
 * §8), so a `_start` that returns faults, and the launcher reads `Faulted`
 * where it expected `Exited`. `_start` must therefore not be `_Noreturn` and
 * must not spin: an image that cannot reach its own Process object should
 * die rather than hang the case.
 */

#include "sos.h"

void _start(sos_word boot_handle) {
    /* The exit code is `main`'s return value; the launcher reads it back out
     * of the §8 status word as `Exited`(1) << 16 | code. */
    int code = main(boot_handle);

    /* A child bootstraps as root does (spec §12): the System handle its
     * launcher gave it is in the register above, and its own Process object
     * is derived from that. `Process.exit` is how it ends itself. */
    sos_word process = 0;
    if (sos_system_process_self(boot_handle, SOS_ALL_RIGHTS, &process)
            == SOS_STATUS_OK) {
        (void)sos_process_exit(process, (sos_word)code);
    }

    /* Unreached on success: `exit` does not return. Reaching here means the
     * boot handle was not a System handle with `ProcessSelf`, and the return
     * below is the fault the header describes. */
}
