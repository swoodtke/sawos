/* SOS from C — the whole of what a freestanding C process needs (sawos design
 * 39, the C-leg probe).
 *
 * **THIS FILE IS THE FINDING.** It is what a C program has to write down for
 * itself today in order to reach the kernel, and every line of it is either the
 * published ABI (`hal/<arch>/user/ABI.md`) or a number the tree calls
 * KERNEL-INTERNAL. The second kind is the interesting kind — see the op block
 * below and design 39's As-built.
 *
 * Nothing here is linked from the tree. The probe deliberately re-derives the
 * syscall seam from the ABI documents rather than naming
 * `hal/<arch>/user/syscall.c`, because the question the unit was dispatched to
 * answer is what a NON-Saw image costs when it starts from the documents — and
 * the answer is only honest if the image builds out of its own sources.
 */

#ifndef SOS_H
#define SOS_H

/* THE WORD IS THE REGISTER, AND ONE TYPEDEF COVERS BOTH PROFILES. `unsigned
 * long` is 32 bits under riscv32's ilp32 and 64 bits under aarch64's LP64, so
 * it is the same width as the machine's argument register on each — which is
 * the same claim `hal/arm64/user/ABI.md` makes about Saw's `UInt`. The two HAL
 * stubs say `unsigned int` and `unsigned long` respectively; that is two
 * spellings of this one, not two contracts. */
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
 * The op numbers — AND THIS BLOCK IS WHAT DESIGN 31 PART 1 EXISTS TO DELETE
 * ------------------------------------------------------------------------- */
/* spec §5.7's vDSO discipline says an op number is not ABI: userspace names the
 * op and the `sos` module supplies the number, so a renumbering is a rebuild
 * rather than a break. A C image cannot hold up its end of that bargain today.
 * The typed C surface it is supposed to use — `sos_system_debug_print`,
 * `sos_system_process_self`, `sos_process_exit` and the rest of
 * `kernel/sysapi/src/floor.saw`'s `@export`ed block — is compiled out of a SAW
 * module, so reaching it means linking `sos`, which means linking `sosrt`,
 * which means the image is no longer a C image. So this probe writes the
 * numbers, out of `kernel/abi/src/ops.saw`, a module whose own header calls
 * itself KERNEL-INTERNAL.
 *
 * THE COST OF BEING WRONG IS NOT A LINK ERROR. An op number the kernel does not
 * recognize on that object is a caller error the process could have checked, so
 * design 178's faults ruling terminates the process — the image builds, boots,
 * and dies. `tests/riscv32/payload_sosimg.S` is the existing precedent for
 * hardcoding two of these; this is the same debt at three. */
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
 * The two ops this probe needs, named
 * ------------------------------------------------------------------------- */

/* Write ONE byte to the debug console. Requires `SystemRight.Debug`.
 *
 * ONE BYTE PER SYSCALL is the shape, not an omission of this probe's — the Saw
 * `System.debug_print(String)` above it is a loop over exactly this call. A
 * libc's `write(2)` cannot be built on it at any sane cost; see design 39's
 * As-built finding 6. */
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
