// SOS riscv32 user-side HAL: the syscall instruction (designs 140, 172).
//
// This is the ENTIRE architecture-dependent surface of an SOS process. An
// `ecall` is not expressible in Saw, and neither is naming the registers the
// ABI puts arguments in, so this one file exists — and nothing else in a
// process needs to know it is riscv32.
//
// WHY THIS IS C, and it is the only reason left (design 172's reason sweep):
// the `ecall` INSTRUCTION plus the register pinning the ABI requires. Neither
// has a Saw spelling and neither will without inline asm, which design 172
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
// `sos/hal/riscv32/kernel/` is the kernel's counterpart; `sos/hal/arm64/user/`
// is this file for Profile B, and is now the same six lines.
//
// ABI (sos/spec.md §5.7): a0 = HANDLE, a7 = OP, args a1-a5, `ecall`; returns
// a0 = status word, a1 = value. Every syscall is an object op, so there is no
// form of this that does not take a handle.

typedef unsigned int u32;

// One argument covers every M1 op (`debug_print` takes a character,
// `shutdown` a status). Wider forms add a2-a5 the same way.
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
