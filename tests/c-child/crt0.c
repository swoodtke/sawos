/* SOS from C: the crt0 (sawos design 39, the C-leg probe).
 *
 * **IT IS A PLAIN C FUNCTION, THERE IS NO ASSEMBLY IN IT, AND IT IS THE SAME
 * FILE ON BOTH PROFILES.** That is finding 2, and it is the probe's happiest
 * result: what the kernel hands a process at entry IS the C calling convention
 * already.
 *
 *   - The ENTRY is `_start`, named by every user linker script's `ENTRY(_start)`
 *     and carried into the sosimg header as the image's entry address.
 *   - The BOOT HANDLE arrives in the first ARGUMENT register (a0 / x0), which
 *     both `hal/<arch>/user/ABI.md` state, so a C function of one `unsigned long`
 *     parameter receives it with nothing written.
 *   - The STACK POINTER is already set: `process_start` puts the destination
 *     region's `link_top()` in it (`kernel/core/dispatch.saw`), which every
 *     user linker script stops 16 KiB short of, and the kernel grants that gap
 *     RW separately. Both profiles' region tops are 16-byte aligned, which is
 *     what AArch64's sp alignment rule wants. So there is no stack to set up.
 *   - `.bss` IS ALREADY ZERO: a segment's `mem_len` exceeds its `file_len` and
 *     the kernel zero-fills the tail (`kernel/core/loader.saw`), so a crt0 has
 *     no bss loop to write. `.data` needs no copy either — the loader places
 *     each segment at its link address rather than at a load address a startup
 *     file has to relocate from.
 *
 * WHAT IS LEFT is what this file does: call `main`, and turn its return value
 * into a `Process.exit`.
 *
 * **FALLING OFF `_start` IS THE DOCUMENTED DEATH, and this file uses it as its
 * error path.** The kernel enters a process with a zero link register (spec §8),
 * so a `_start` that returns faults, and the launcher reads `Faulted` where it
 * expected `Exited` — `tests/echo-child/`'s header states the same contract for
 * the Saw children. A C `_start` therefore must NOT be `_Noreturn` and must not
 * spin: an image that cannot reach its own Process object should die loudly
 * rather than hang the case.
 */

#include "sos.h"

void _start(sos_word boot_handle) {
    /* THE EXIT CODE IS `main`'s RETURN VALUE, which is the one piece of POSIX
     * shape this probe implements: the launcher reads it back out of the §8
     * status word as `Exited`(1) << 16 | code. */
    int code = main(boot_handle);

    /* A child bootstraps exactly as root does (spec §12's symmetry): the System
     * handle its launcher gave it is in the register above, and its own Process
     * object is derived from that. `Process.exit` is the only door out. */
    sos_word process = 0;
    if (sos_system_process_self(boot_handle, SOS_ALL_RIGHTS, &process)
            == SOS_STATUS_OK) {
        (void)sos_process_exit(process, (sos_word)code);
    }

    /* Unreached on success — `exit` does not return. Reaching here means the
     * boot handle was not a System handle with `ProcessSelf`, and the return
     * below is the fault this file's header describes. */
}
