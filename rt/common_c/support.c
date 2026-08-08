// SOS common runtime support (designs 140, 172) — the C that must stay C, once.
//
// EVERY LINE IN THIS FILE IS C FOR ONE OF TWO REASONS, and both are PERMANENT.
// Nothing here is waiting on a language feature; when this file was written it
// also held a bump arena and the four `__saw_rt_*` seams, and design 172 part 2
// moved those to Saw (`sos/rt/common/src/lib.saw`), which is what leaves the
// two reasons below as the whole story.
//
//  1. mem* : a byte-copy loop written in Saw is exactly the pattern LLVM's
//     loop-idiom recognizer rewrites into a call to `memcpy` — which, in a
//     freestanding build where this IS memcpy, is a call to itself. Keeping
//     these in C lets them be compiled with `-fno-builtin`, which is the
//     supported way to say "do not do that". (Same reason libcs write them in
//     assembly or with the same flag.)
//
//  2. the __atomic_* family : a compiler-generated LIBCALL, so it must exist
//     under the name and signature the backend emits, for a build that does not
//     name an atomics extension. There is nothing for Saw to express — the
//     caller is codegen, not source. See the uniprocessor caveat below.
//
// So this file has no seam of its own any more and declares no hook. The two
// per-side hooks it used to be written against — `sos_rt_write` and
// `sos_rt_abort`, where a byte goes and how the machine stops — are still the
// system's one runtime seam; they are just Saw on both ends now. See
// `sos/rt/common/src/lib.saw` for that contract, and `sos/spec.md` §5c for the
// C floor as a whole.
//
// What design 172 moved out of the SOS C layer over its two parts: the board
// consoles, the machine stops, the arm64 page tables, the PMP region staging,
// the kernel-fault report, the runtime seams, the arena, and the process side's
// console/abort hooks. What is left, across the whole tree, is this file plus
// four inline-asm leaves.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // ilp32: 32-bit, matches platform `Int`

// ---- mem* (the compiler's implicit block-copy / zero helpers) -------------
//
// Compiled with -fno-builtin; see reason 1 in the header.

void *memset(void *dst, int c, usize n) {
    u8 *d = (u8 *)dst;
    for (usize i = 0; i < n; i++) d[i] = (u8)c;
    return dst;
}

void *memcpy(void *dst, const void *src, usize n) {
    u8 *d = (u8 *)dst; const u8 *s = (const u8 *)src;
    for (usize i = 0; i < n; i++) d[i] = s[i];
    return dst;
}

void *memmove(void *dst, const void *src, usize n) {
    u8 *d = (u8 *)dst; const u8 *s = (const u8 *)src;
    if (d < s) {
        for (usize i = 0; i < n; i++) d[i] = s[i];
    } else {
        for (usize i = n; i > 0; i--) d[i - 1] = s[i - 1];
    }
    return dst;
}

// ---- atomic libcalls, by width -------------------------------------------
//
// CURRENTLY UNREFERENCED, and kept deliberately. Both builds now pass
// `--target-features +m,+a,+c`, so String's refcount traffic lowers to real
// `amoadd.w` instructions and `--gc-sections` drops everything below. They stay
// because a build that does NOT name the A extension — a smaller part, a
// different profile — lowers the same traffic to these libcalls instead, and
// the alternative to keeping them is an unresolved-symbol link failure with no
// hint as to what to write.
//
// THE UNIPROCESSOR CAVEAT, STATED ONCE FOR THE WHOLE FAMILY: plain
// read-modify-write is correct here and nowhere else. SOS v1 is a uniprocessor
// kernel (spec §7) and M1 enables no interrupt sources, so no other agent can
// observe the window. THREE later changes each invalidate every function
// below and must replace them together: enabling interrupts (an ISR touching a
// refcount), SMP, and preemption between threads sharing a refcount. Building
// the Saw object for an ISA with atomics retires them outright.
//
// The _4 family is what a 32-bit target needs. arm64 (M1b) adds _8 beside it —
// the reason these are grouped as a family rather than written as four
// one-offs.

u32 __atomic_load_4(const volatile void *ptr, int memorder) {
    (void)memorder;
    return *(const volatile u32 *)ptr;
}

void __atomic_store_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    *(volatile u32 *)ptr = val;
}

u32 __atomic_fetch_add_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    volatile u32 *p = (volatile u32 *)ptr;
    u32 old = *p;
    *p = old + val;
    return old;
}

u32 __atomic_fetch_sub_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    volatile u32 *p = (volatile u32 *)ptr;
    u32 old = *p;
    *p = old - val;
    return old;
}
