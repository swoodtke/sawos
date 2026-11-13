// SOS Xtensa user-side HAL: the syscall instruction, ESP32-S3 board, call0.
//
// This is the entire architecture-dependent surface of an SOS process. A
// `syscall` is not expressible in Saw, and neither is naming the registers the
// ABI puts arguments in, so this file exists and nothing else in a process
// needs to know it is Xtensa. It is C because of the `syscall` instruction
// plus the register pinning the ABI requires; neither has a Saw spelling.
//
// The runtime's hooks name no architecture (a byte reaches the console through
// a System op, the same op on every profile), so they live in the arch-free
// Saw module in `kernel/sysapi/`, not here.
//
// ABI (spec.md §5.7): a2 = handle, a8 = op, args a3-a5, `syscall`; returns
// a2 = status word, a3 = value. The handle and the arguments are a clean run
// of call0's argument registers and the op sits outside it, the shape the
// other profiles keep. Every syscall is an object op, so there is no form of
// this that does not take a handle.
//
// Built call0 with no FPU, as every image on this board is: libgcc and the
// ROM's helpers are windowed, and nothing here may reach them.

typedef unsigned int u32;

// One argument covers the two System ops that answer with a status alone
// (`debug_print` takes a character, `shutdown` a status).
u32 sos_syscall1(u32 handle, u32 op, u32 arg0) {
    register u32 a2 __asm__("a2") = handle;
    register u32 a3 __asm__("a3") = arg0;
    register u32 a8 __asm__("a8") = op;
    __asm__ volatile("syscall"
                     : "+a"(a2), "+a"(a3)
                     : "a"(a8)
                     : "memory");
    return a2;
}

// Three arguments and the value register, for the ops that answer with one: a
// created thread's handle, a joined thread's exit code, a process's status
// word. The value comes back through a pointer rather than in the return,
// because the Saw side declares these symbols against a C ABI whose whitelist
// has no aggregate return. Ops with fewer arguments pass zeros.
u32 sos_syscall3(u32 handle, u32 op, u32 arg0, u32 arg1, u32 arg2,
                 u32 *value_out) {
    register u32 a2 __asm__("a2") = handle;
    register u32 a3 __asm__("a3") = arg0;
    register u32 a4 __asm__("a4") = arg1;
    register u32 a5 __asm__("a5") = arg2;
    register u32 a8 __asm__("a8") = op;
    __asm__ volatile("syscall"
                     : "+a"(a2), "+a"(a3)
                     : "a"(a4), "a"(a5), "a"(a8)
                     : "memory");
    *value_out = a3;
    return a2;
}
