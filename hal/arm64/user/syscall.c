// SOS arm64 user-side HAL: the syscall instruction (designs 162, 172).
//
// This is the ENTIRE architecture-dependent surface of an SOS process. An `svc`
// is not expressible in Saw, and neither is naming the registers the ABI puts
// arguments in, so this one file exists — and nothing else in a process needs
// to know it is arm64.
//
// WHY EACH THING HERE IS C (design 172's reason sweep). The kernel side of
// this HAL went almost entirely to Saw; the process side did not, and the
// reasons are different for the two halves of the file:
//
//   - `sos_syscall1` : the `svc` INSTRUCTION plus the register pinning the ABI
//     requires. Neither has a Saw spelling and neither will without inline asm,
//     which design 172 explicitly does not open. PERMANENT as written.
//   - the two hooks + the parked handle : these ARE expressible. They name no
//     architecture — a byte reaches the console through a System op, which is
//     the same op on both profiles — so they belong in ONE arch-free Saw
//     module, not in two per-arch C files. What stops it today is the same
//     thing that stopped design 172 unit 2 (DF-172e): the runtime seams they
//     serve include a `noreturn` panic sink Saw cannot type. When that lands,
//     this file should be `sos_syscall1` and nothing else.
//
// `sos/hal/arm64/kernel/` is the kernel's counterpart.
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

// One argument covers every M1 op (`debug_print` takes a character,
// `shutdown` a status). Wider forms add x2-x5 the same way.
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

// ---- the two hooks the common runtime calls -------------------------------
//
// A process owns no device, so both hooks are System ops. That is the whole
// point of the privilege split: if `panic()` could reach a UART directly, the
// split would not be real.
//
// The runtime seams take no handle, so the one root is given at boot is parked
// here by `sos_set_system_handle` before anything can print. It is root's own
// authority being remembered in root's own address space — not ambient
// authority, because a process that was never given the handle has nothing to
// remember and its panics are simply silent.

// These are the kernel package's `@export`ed C-ABI surface (the `sos` module,
// sos/kernel/sysapi/) — the SUPPORTED interface for non-Saw callers. The sinks
// below go through them rather than through `sos_syscall1` directly, which is
// the point: no op number appears in this file, or anywhere outside the kernel
// package. It also means the C altitude is exercised on every boot rather than
// only by a test.
u64 sos_system_debug_print(u64 handle, u64 byte);
u64 sos_system_shutdown(u64 handle, u64 status);

typedef unsigned int u32;
typedef unsigned long usize;

static u64 system_handle = 0;

void sos_set_system_handle(u64 handle) {
    system_handle = handle;
}

void sos_rt_write(const char *ptr, usize len) {
    if (system_handle == 0) {
        return;
    }
    for (usize i = 0; i < len; i++) {
        sos_system_debug_print(system_handle, (u64)(unsigned char)ptr[i]);
    }
}

__attribute__((noreturn))
void sos_rt_abort(u32 code) {
    sos_system_shutdown(system_handle, (u64)code);
    // `shutdown` does not return. If the handle was never set, or the right
    // was stripped, there is nothing left to try — do not run off the end of
    // the granted region.
    for (;;) { }
}
