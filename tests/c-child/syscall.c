/* The trap instruction for both profiles, hand-rolled.
 *
 * One file, two architectures; the `#if` is the only architectural thing in
 * it. The tree keeps two copies, `hal/arm64/user/syscall.c` and
 * `hal/riscv32-common/user/syscall.c`, because a Saw image reaches them
 * through a manifest's per-triple `native` line. A C image has the
 * preprocessor and does not need the split.
 *
 * This duplicates the HAL stubs on purpose: the image builds out of its own
 * sources, writing the seam from `hal/<arch>/user/ABI.md` alone.
 *
 * ABI (spec §5.7), the two rows this file implements:
 *
 *   riscv32   a0 = handle (in) / status (out), a7 = op, a1-a5 args,
 *             a1 also carries the value half on return.  `ecall`
 *   arm64     x0 = handle (in) / status (out), x8 = op, x1-x5 args,
 *             x1 also carries the value half on return.  `svc #0`
 */

#include "sos.h"

#if defined(__aarch64__)

sos_word sos_syscall1(sos_word handle, sos_word op, sos_word arg0) {
    register sos_word x0 __asm__("x0") = handle;
    register sos_word x1 __asm__("x1") = arg0;
    register sos_word x8 __asm__("x8") = op;
    __asm__ volatile("svc #0"
                     : "+r"(x0), "+r"(x1)
                     : "r"(x8)
                     : "memory");
    return x0;
}

sos_word sos_syscall3(sos_word handle, sos_word op, sos_word arg0, sos_word arg1,
                      sos_word arg2, sos_word *value_out) {
    register sos_word x0 __asm__("x0") = handle;
    register sos_word x1 __asm__("x1") = arg0;
    register sos_word x2 __asm__("x2") = arg1;
    register sos_word x3 __asm__("x3") = arg2;
    register sos_word x8 __asm__("x8") = op;
    __asm__ volatile("svc #0"
                     : "+r"(x0), "+r"(x1)
                     : "r"(x2), "r"(x3), "r"(x8)
                     : "memory");
    *value_out = x1;
    return x0;
}

#elif defined(__riscv)

sos_word sos_syscall1(sos_word handle, sos_word op, sos_word arg0) {
    register sos_word a0 __asm__("a0") = handle;
    register sos_word a1 __asm__("a1") = arg0;
    register sos_word a7 __asm__("a7") = op;
    __asm__ volatile("ecall"
                     : "+r"(a0), "+r"(a1)
                     : "r"(a7)
                     : "memory");
    return a0;
}

sos_word sos_syscall3(sos_word handle, sos_word op, sos_word arg0, sos_word arg1,
                      sos_word arg2, sos_word *value_out) {
    register sos_word a0 __asm__("a0") = handle;
    register sos_word a1 __asm__("a1") = arg0;
    register sos_word a2 __asm__("a2") = arg1;
    register sos_word a3 __asm__("a3") = arg2;
    register sos_word a7 __asm__("a7") = op;
    __asm__ volatile("ecall"
                     : "+r"(a0), "+r"(a1)
                     : "r"(a2), "r"(a3), "r"(a7)
                     : "memory");
    *value_out = a1;
    return a0;
}

#else
#error "SOS targets riscv32 and aarch64; this file names no other trap instruction"
#endif

/* ---------------------------------------------------------------------------
 * The named ops. Not architectural: a byte reaches the console through a
 * System op, which is the same op on both profiles.
 * -------------------------------------------------------------------------- */

sos_word sos_system_debug_print(sos_word system, sos_word byte) {
    return sos_syscall1(system, SOS_OP_SYSTEM_DEBUG_PRINT, byte);
}

sos_word sos_system_process_self(sos_word system, sos_word keep,
                                 sos_word *handle_out) {
    return sos_syscall3(system, SOS_OP_SYSTEM_PROCESS_SELF, keep, 0, 0,
                        handle_out);
}

sos_word sos_process_exit(sos_word process, sos_word code) {
    return sos_syscall1(process, SOS_OP_PROCESS_EXIT, code);
}
