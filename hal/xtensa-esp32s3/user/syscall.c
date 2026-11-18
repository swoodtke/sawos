// SOS Xtensa user-side HAL: the syscall instruction and the thread register,
// ESP32-S3 board.
//
// This is the entire architecture-dependent surface of an SOS process. A
// `syscall` is not expressible in Saw, and neither is naming the registers the
// ABI puts arguments in, so this file exists and nothing else in a process
// needs to know it is Xtensa. It is C because of the `syscall` instruction
// plus the register pinning the ABI requires, and the thread register's
// accessors at the foot; none has a Saw spelling.
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
// Built with no FPU, call0 or windowed as the image that links it is (a
// package's `target-features = "+windowed"`, ../ABI.md §7d). Either way the
// register variables name the registers of the window that executes
// `syscall`, which are the ones the kernel reads. A call0 image must not reach
// libgcc or the ROM's helpers, which are windowed; nothing here does.

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

// The thread register, `THREADPTR`. The kernel saves and restores it in a
// thread's frame and starts a new thread with it zero, so it is the thread's
// own word; nothing the compilers emit here uses it. The runtime keeps the
// thread's lock-owner identity in it (`kernel/sysapi/src/rt.saw`).
u32 sos_thread_word(void) {
    u32 word;
    __asm__ volatile("rur %0, threadptr" : "=a"(word));
    return word;
}

void sos_set_thread_word(u32 word) {
    __asm__ volatile("wur %0, threadptr" : : "a"(word));
}
