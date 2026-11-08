/* Everything a freestanding C process needs to reach the kernel. Each line is
 * either the published ABI (`hal/<arch>/user/ABI.md`) or an op number from
 * the kernel-internal numbering (see the op block below).
 *
 * Nothing here is linked from the tree: the syscall seam is re-derived from
 * the ABI documents rather than taken from `hal/<arch>/user/syscall.c`, so
 * the image builds out of its own sources.
 */

#ifndef SOS_H
#define SOS_H

/* The word is the register, and one typedef covers both profiles. `unsigned
 * long` is 32 bits under riscv32's ilp32 and 64 bits under aarch64's LP64, the
 * same width as the machine's argument register on each, as with Saw's `UInt`
 * in `hal/arm64/user/ABI.md`. The two HAL stubs spell it `unsigned int` and
 * `unsigned long` respectively; that is one contract. */
typedef unsigned long sos_word;

/* -------------------------------------------------------------------------
 * The raw seam (spec §5.7, `hal/riscv32/user/ABI.md` + `hal/arm64/user/ABI.md`)
 * ------------------------------------------------------------------------- */

/* One object op that answers with a status alone. */
sos_word sos_syscall1(sos_word handle, sos_word op, sos_word arg0);

/* The same, for an op that takes up to three arguments AND answers with a
 * value. The value comes back through a pointer for the reason both ABI
 * documents give: the C ABI these symbols are declared against has no
 * aggregate return. */
sos_word sos_syscall3(sos_word handle, sos_word op, sos_word arg0, sos_word arg1,
                      sos_word arg2, sos_word *value_out);

/* -------------------------------------------------------------------------
 * The op numbers
 * ------------------------------------------------------------------------- */
/* spec §5.7's vDSO discipline says an op number is not ABI: userspace names
 * the op and the `sos` module supplies the number. A C image cannot do that.
 * The typed C surface (`sos_system_debug_print`, `sos_system_process_self`,
 * `sos_process_exit` and the rest of `kernel/sysapi/src/floor.saw`'s
 * `@export`ed block) is compiled out of a Saw module, so reaching it means
 * linking `sos` and `sosrt`, and the image is no longer a C image. So these
 * numbers are copied from `kernel/abi/src/ops.saw`, which is kernel-internal.
 *
 * A wrong number is not a link error. An op number the kernel does not
 * recognize on that object is a caller error, which terminates the process:
 * the image builds, boots, and dies. */
#define SOS_OP_SYSTEM_DEBUG_PRINT  0u  /* SystemOp.DebugPrint  */
#define SOS_OP_SYSTEM_PROCESS_SELF 2u  /* SystemOp.ProcessSelf */
#define SOS_OP_PROCESS_EXIT        2u  /* ProcessOp.Exit       */

/* `sos.floor.ALL_RIGHTS` — the keep mask `System.process_self()` defaults to.
 * A mask, not a right: it says which of the rights the kernel would mint the
 * new Process handle with are kept. */
#define SOS_ALL_RIGHTS 0xFFFFFFFFu

/* A status of 0 is success (spec §5.7). Anything else is a `SosStatus` tag. */
#define SOS_STATUS_OK 0u

/* -------------------------------------------------------------------------
 * The ops this image needs, named
 * ------------------------------------------------------------------------- */

/* Write one byte to the debug console. Requires `SystemRight.Debug`. One byte
 * per syscall is the op's shape; the Saw `System.debug_print(String)` is a
 * loop over this call. */
sos_word sos_system_debug_print(sos_word system, sos_word byte);

/* This process's own Process handle, out of its System handle. Requires
 * `SystemRight.ProcessSelf`. */
sos_word sos_system_process_self(sos_word system, sos_word keep,
                                 sos_word *handle_out);

/* End this process with `code`. Requires `ProcessRight.Exit`. Does not return
 * on success. */
sos_word sos_process_exit(sos_word process, sos_word code);

/* The program. `crt0.c` calls it with the boot handle the kernel left in the
 * first argument register, and takes its return value as the exit code. */
int main(sos_word boot_handle);

#endif /* SOS_H */
