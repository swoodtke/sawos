// SOS M0 runtime seams (design 112) — riscv32, QEMU `virt`.
//
// sawc's freestanding profile emits the runtime seams (saw_alloc / saw_dealloc /
// saw_write / saw_panic) as DECLARATIONS; the environment supplies the bodies.
// On the QEMU `virt` board that environment is this file plus boot.S. `saw_write`
// and `saw_panic` drive the same NS16550A UART the Saw driver uses (0x1000_0000);
// `saw_panic` additionally FAILS the run through the SiFive test finisher. A
// bump allocator over a fixed .bss arena backs `saw_alloc` (enough for panic
// message assembly), and the mem* helpers cover the compiler's implicit calls.
//
// The `saw_*` symbols are RESERVED (sawc rejects `@export("saw_*")`), so these
// seams live in C, not Saw — the freestanding runtime boundary by design.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // ilp32: 32-bit, matches platform `Int`

#define UART_BASE    0x10000000u
#define UART_THR     0x0u
#define UART_LSR     0x5u
#define LSR_TX_EMPTY 0x20u          // LSR bit 5 — TX holding register empty
#define SIFIVE_TEST  0x00100000u
#define FINISH_FAIL  0x3333u

static void uart_putc(u8 c) {
    volatile u8 *lsr = (volatile u8 *)(UART_BASE + UART_LSR);
    volatile u8 *thr = (volatile u8 *)(UART_BASE + UART_THR);
    while ((*lsr & LSR_TX_EMPTY) == 0) { }
    *thr = c;
}

static void uart_write(const char *p, usize len) {
    for (usize i = 0; i < len; i++) uart_putc((u8)p[i]);
}

// ---- runtime seams --------------------------------------------------------

void saw_write(const char *ptr, usize len) {
    uart_write(ptr, len);
}

__attribute__((noreturn))
void saw_panic(const char *msg, usize len) {
    uart_write(msg, len);
    // FINISHER_FAIL with code 1 in the upper half → non-zero emulator exit.
    volatile u32 *test = (volatile u32 *)SIFIVE_TEST;
    *test = FINISH_FAIL | (1u << 16);
    for (;;) { __asm__ volatile("wfi"); }
}

// Bump allocator over a fixed .bss arena — sized for panic-message assembly and
// any incidental M0 allocation. No reclamation (saw_dealloc is a no-op); the
// kernel object model brings the real slab allocator in a later brief.
#define ARENA_BYTES (64u * 1024u)
static u8    arena[ARENA_BYTES];
static usize arena_next = 0;

void *saw_alloc(usize size, usize align) {
    if (align < 1) align = 1;
    usize p = (arena_next + (align - 1)) & ~(align - 1);
    if (p + size > ARENA_BYTES) {
        saw_panic("sos: out of arena memory\n", 25);
    }
    arena_next = p + size;
    return &arena[p];
}

void saw_dealloc(void *ptr, usize size, usize align) {
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
