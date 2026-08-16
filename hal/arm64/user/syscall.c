// SOS arm64 user-side HAL: the syscall instruction (designs 162, 172).
//
// This is the ENTIRE architecture-dependent surface of an SOS process. An `svc`
// is not expressible in Saw, and neither is naming the registers the ABI puts
// arguments in, so this one file exists — and nothing else in a process needs
// to know it is arm64.
//
// WHY THIS IS C, and it is the only reason left (design 172's reason sweep):
// the `svc` INSTRUCTION plus the register pinning the ABI requires. Neither has
// a Saw spelling and neither will without inline asm, which design 172
// explicitly does not open. PERMANENT as written.
//
// This file used to carry the runtime's two hooks and a parked handle beside
// the stub, and its own header said it should be `sos_syscall1` and nothing
// else as soon as DF-172e closed. Design 177 closed it, so design 172 part 2
// moved them: they name no architecture — a byte reaches the console through a
// System op, which is the same op on both profiles — so two per-arch C copies
// were two copies of one thing. They are one arch-free Saw module now, in
// `sos/kernel/sysapi/`, beside the System object whose authority they use.
//
// `sos/hal/arm64/kernel/` is the kernel's counterpart; `sos/hal/riscv32/user/`
// is this file for Profile A, and is now the same six lines.
//
// ABI (sos/spec.md §5.7): x0 = HANDLE, x8 = OP, args x1-x5, `svc #0`; returns
// x0 = status word, x1 = value. Every syscall is an object op, so there is no
// form of this that does not take a handle.
//
// WHY x8 AND NOT x6 (design 162 decision 1). The op could have gone in the
// first free argument register, but x8 is where AArch64 Linux puts a syscall
// number, so every disassembler, every debugger script and every reader who has
// seen one arm64 syscall already reads this one correctly — and it leaves
// x0-x5 as a clean run of argument registers. The RISC-V profile makes the same
// trade with a7.
//
// WIDTH: the registers are 64-bit and so is this process's `UInt`, so the
// declarations Saw makes against these symbols line up with no truncation
// anywhere. The riscv32 counterpart says `unsigned int` for exactly the same
// reason.

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

// Three arguments AND the value register, for the ops that answer with one: a
// created thread's handle, a joined thread's exit code, a process's status word
// (design 178 M2 unit 2). The value comes back through a pointer rather than in
// the return, because the Saw side declares these symbols against a C ABI whose
// whitelist has no aggregate return — one out-parameter is the shape that
// crosses. Ops with fewer arguments pass zeros.
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
