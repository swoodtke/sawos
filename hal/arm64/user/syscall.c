// SOS arm64 user-side HAL: the syscall instruction.
//
// This is the entire architecture-dependent surface of an SOS process. An `svc`
// is not expressible in Saw, and neither is naming the registers the ABI puts
// arguments in, so this file exists and nothing else in a process needs to
// know it is arm64. It is C because of the `svc` instruction plus the register
// pinning the ABI requires; neither has a Saw spelling.
//
// The runtime's hooks name no architecture (a byte reaches the console through
// a System op, the same op on every profile), so they live in the arch-free
// Saw module in `kernel/sysapi/`, not here.
//
// `hal/arm64/kernel/` is the kernel's counterpart; `hal/riscv32-common/user/`
// is this file for riscv32.
//
// ABI (spec.md §5.7): x0 = handle, x8 = op, args x1-x3, `svc #0`; returns
// x0 = status word, x1 = value. Every syscall is an object op, so there is no
// form of this that does not take a handle.
//
// The op goes in x8, not the first free argument register, because x8 is where
// AArch64 Linux puts a syscall number, so disassemblers and debugger scripts
// read it correctly, and x0-x3 stay a clean run of argument registers. The
// riscv32 profile makes the same trade with a7.
//
// Width: the registers are 64-bit and so is this process's `UInt`, so Saw's
// declarations against these symbols line up with no truncation. The riscv32
// counterpart says `unsigned int` for the same reason.

typedef unsigned long u64;

// One argument covers the two System ops that answer with a status alone
// (`debug_print` takes a character, `shutdown` a status).
u64 sos_syscall1(u64 handle, u64 op, u64 arg0) {
    register u64 x0 __asm__("x0") = handle;
    register u64 x1 __asm__("x1") = arg0;
    register u64 x8 __asm__("x8") = op;
    __asm__ volatile("svc #0"
                     : "+r"(x0), "+r"(x1)
                     : "r"(x8)
                     : "memory");
    return x0;
}

// Three arguments and the value register, for the ops that answer with one: a
// created thread's handle, a joined thread's exit code, a process's status
// word. The value comes back through a pointer rather than in the return,
// because the Saw side declares these symbols against a C ABI whose whitelist
// has no aggregate return. Ops with fewer arguments pass zeros.
u64 sos_syscall3(u64 handle, u64 op, u64 arg0, u64 arg1, u64 arg2, u64 *value_out) {
    register u64 x0 __asm__("x0") = handle;
    register u64 x1 __asm__("x1") = arg0;
    register u64 x2 __asm__("x2") = arg1;
    register u64 x3 __asm__("x3") = arg2;
    register u64 x8 __asm__("x8") = op;
    __asm__ volatile("svc #0"
                     : "+r"(x0), "+r"(x1)
                     : "r"(x2), "r"(x3), "r"(x8)
                     : "memory");
    *value_out = x1;
    return x0;
}
