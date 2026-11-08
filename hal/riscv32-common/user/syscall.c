// SOS riscv32 user-side HAL: the syscall instruction, shared by every riscv32
// board.
//
// This is the entire architecture-dependent surface of an SOS process. An
// `ecall` is not expressible in Saw, and neither is naming the registers the
// ABI puts arguments in, so this file exists and nothing else in a process
// needs to know it is riscv32. It is C because of the `ecall` instruction plus
// the register pinning the ABI requires; neither has a Saw spelling.
//
// It names the ISA's syscall instruction and the ABI's registers and nothing
// about a board, so every root and child manifest, whichever board it targets,
// names this path in its `[sos.riscv32-unknown-none-elf] native` line.
//
// The runtime's hooks name no architecture (a byte reaches the console through
// a System op, the same op on every profile), so they live in the arch-free
// Saw module in `kernel/sysapi/`, not here.
//
// `../kernel/sink.c` is the kernel's counterpart, shared the same way;
// `hal/arm64/user/` is this file for arm64.
//
// ABI (spec.md §5.7): a0 = handle, a7 = op, args a1-a3, `ecall`; returns
// a0 = status word, a1 = value. Every syscall is an object op, so there is no
// form of this that does not take a handle.

typedef unsigned int u32;

// One argument covers the two System ops that answer with a status alone
// (`debug_print` takes a character, `shutdown` a status).
u32 sos_syscall1(u32 handle, u32 op, u32 arg0) {
    register u32 a0 __asm__("a0") = handle;
    register u32 a1 __asm__("a1") = arg0;
    register u32 a7 __asm__("a7") = op;
    __asm__ volatile("ecall"
                     : "+r"(a0), "+r"(a1)
                     : "r"(a7)
                     : "memory");
    return a0;
}

// Three arguments and the value register, for the ops that answer with one: a
// created thread's handle, a joined thread's exit code, a process's status
// word. The value comes back through a pointer rather than in the return,
// because the Saw side declares these symbols against a C ABI whose whitelist
// has no aggregate return. Ops with fewer arguments pass zeros.
u32 sos_syscall3(u32 handle, u32 op, u32 arg0, u32 arg1, u32 arg2, u32 *value_out) {
    register u32 a0 __asm__("a0") = handle;
    register u32 a1 __asm__("a1") = arg0;
    register u32 a2 __asm__("a2") = arg1;
    register u32 a3 __asm__("a3") = arg2;
    register u32 a7 __asm__("a7") = op;
    __asm__ volatile("ecall"
                     : "+r"(a0), "+r"(a1)
                     : "r"(a2), "r"(a3), "r"(a7)
                     : "memory");
    *value_out = a1;
    return a0;
}
