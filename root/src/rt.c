// SOS root server runtime seams (design 140) — riscv32, U-MODE.
//
// The kernel's rt.c drives the UART and the test finisher directly, because the
// kernel owns the machine. Root owns nothing: it holds no PMP grant for any
// device, so every one of these bodies goes through an `ecall`. That is the
// point — if root could reach the UART, the M/U split would not be real.
//
// This file exists at all because an `ecall` is not expressible in Saw. It is
// the same shape as sawc/rt/shim.c's FFI-blocked bodies: the smallest possible
// native surface, everything above it written in Saw.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // ilp32: 32-bit, matches platform `Int`

// The v1 syscall table (sos/spec.md §5.7).
#define SYS_DEBUG_PUTC  0u
#define SYS_EXIT        1u

// Root's own exit codes, distinct from the kernel's.
#define EXIT_PANIC      64u
#define EXIT_NO_MEMORY  65u

// Syscall ABI: number in a7, args a0-a5, returns a0 = status and a1 = value.
// v1 needs only the one-argument form.
static u32 syscall1(u32 number, u32 arg0) {
    register u32 a0 __asm__("a0") = arg0;
    register u32 a7 __asm__("a7") = number;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");
    return a0;
}

u32 sos_debug_putc(u32 c) {
    return syscall1(SYS_DEBUG_PUTC, c);
}

__attribute__((noreturn))
void sos_exit(u32 status) {
    syscall1(SYS_EXIT, status);
    // The kernel does not return from `exit`. If it ever did, do not fall off
    // the end of the granted region.
    for (;;) { }
}

// ---- runtime seams --------------------------------------------------------

void __saw_rt_write(const char *ptr, usize len) {
    for (usize i = 0; i < len; i++) {
        sos_debug_putc((u32)(u8)ptr[i]);
    }
}

__attribute__((noreturn))
void __saw_rt_panic(const char *msg, usize len) {
    __saw_rt_write(msg, len);
    sos_exit(EXIT_PANIC);
}

// A small bump arena. Root v1 allocates nothing on purpose — its banner is a
// string literal walked scalar by scalar — but the compiler's panic path can,
// and an unbacked seam would turn a diagnosable panic into a wild jump.
// Exhaustion is fatal and says so; there is no reclamation (spec §4: the real
// slab allocator arrives with the kernel object model).
#define ARENA_BYTES (4u * 1024u)
static u8    arena[ARENA_BYTES];
static usize arena_next = 0;

void *__saw_rt_alloc(usize size, usize align) {
    if (align < 1) align = 1;
    usize p = (arena_next + (align - 1)) & ~(align - 1);
    if (p + size > ARENA_BYTES) {
        __saw_rt_write("sos-root: out of arena memory\n", 30);
        sos_exit(EXIT_NO_MEMORY);
    }
    arena_next = p + size;
    return &arena[p];
}

void __saw_rt_dealloc(void *ptr, usize size, usize align) {
    (void)ptr; (void)size; (void)align;
}

// ---- mem* (the compiler's implicit block-copy / zero helpers) -------------

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

// ---- atomic libcalls ------------------------------------------------------
//
// As in the kernel: the Saw object is built for bare `rv32i`, so String's
// refcount traffic lowers to libcalls. Root is single-threaded with no
// interrupts, so plain read-modify-write is correct here — and stops being
// correct the moment either changes.

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

u32 __atomic_load_4(const volatile void *ptr, int memorder) {
    (void)memorder;
    return *(const volatile u32 *)ptr;
}

void __atomic_store_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    *(volatile u32 *)ptr = val;
}
