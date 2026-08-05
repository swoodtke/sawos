// SOS common runtime support (design 140) — the C that must stay C, once.
//
// Before this file, sos/kernel/rt.c and sos/root/src/rt.c held ~200 duplicated
// lines: two bump arenas, two sets of mem* helpers, two atomic families, two
// copies of the uniprocessor caveat. They differed in exactly two places —
// where a byte goes and how the machine stops — so those two are the seam:
//
//     void sos_rt_write(const char *ptr, usize len);   // supplied per side
//     void sos_rt_abort(u32 code);                     // supplied per side
//
// The kernel HAL writes to the UART and stops via the test finisher; the user
// HAL writes through a System op and stops via another. Everything else is
// here, once.
//
// WHY THIS IS C AND NOT SAW:
//
//  - mem* : a byte-copy loop written in Saw is exactly the pattern LLVM's
//    loop-idiom recognizer rewrites into a call to `memcpy` — which, in a
//    freestanding build where this IS memcpy, is a call to itself. Keeping
//    these in C lets them be compiled with `-fno-builtin`, which is the
//    supported way to say "do not do that". (Same reason libcs write them in
//    assembly or with the same flag.)
//  - the arena : Saw has no mutable module state beyond `Atomic`, and no way
//    to reserve a `.bss` region from source, so a bump allocator cannot be
//    expressed. See DF-140g.
//  - the seams : `__saw_rt_*` are reserved symbols that only `--runtime-build`
//    may `@export`, and that mode is for authoring `sawc/rt/`, not for a
//    kernel. See DF-140g.
//
// The arch-free, role-free logic that CAN be Saw is Saw: sos/rt/common/.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // ilp32: 32-bit, matches platform `Int`

// ---- the two per-side hooks ----------------------------------------------

void sos_rt_write(const char *ptr, usize len);

__attribute__((noreturn))
void sos_rt_abort(u32 code);

// Abort codes this file raises on its own behalf. A side's own exit codes live
// with that side; these are the runtime's.
#define ABORT_PANIC      64u
#define ABORT_NO_MEMORY  65u

// ---- runtime seams --------------------------------------------------------

void __saw_rt_write(const char *ptr, usize len) {
    sos_rt_write(ptr, len);
}

__attribute__((noreturn))
void __saw_rt_panic(const char *msg, usize len) {
    sos_rt_write(msg, len);
    sos_rt_abort(ABORT_PANIC);
}

// A bump arena over a fixed .bss region. SOS allocates nothing on the steady
// path — the kernel's diagnostics and root's banner are string literals walked
// byte by byte — but the compiler's panic path can allocate, and an unbacked
// seam would turn a diagnosable panic into a wild jump. No reclamation:
// `dealloc` is a no-op and the real slab allocator arrives with the kernel
// object model (spec §4).
#ifndef SOS_ARENA_BYTES
#define SOS_ARENA_BYTES (64u * 1024u)
#endif

static u8    arena[SOS_ARENA_BYTES];
static usize arena_next = 0;

void *__saw_rt_alloc(usize size, usize align) {
    if (align < 1) align = 1;
    usize p = (arena_next + (align - 1)) & ~(align - 1);
    // Checked as a subtraction so a hostile `size` cannot wrap the bound.
    if (size > SOS_ARENA_BYTES || p > SOS_ARENA_BYTES - size) {
        sos_rt_write("sos: out of arena memory\n", 25);
        sos_rt_abort(ABORT_NO_MEMORY);
    }
    arena_next = p + size;
    return &arena[p];
}

void __saw_rt_dealloc(void *ptr, usize size, usize align) {
    (void)ptr; (void)size; (void)align;
}

// ---- mem* (the compiler's implicit block-copy / zero helpers) -------------
//
// Compiled with -fno-builtin; see the header note.

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
