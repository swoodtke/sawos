// SOS M0 runtime seams (design 112) — riscv32, QEMU `virt`.
//
// sawc's freestanding profile emits the runtime-ABI seams (__saw_rt_alloc /
// __saw_rt_dealloc / __saw_rt_write / __saw_rt_panic — the design-113 frozen
// contract, sawc/rt/ABI.md) as DECLARATIONS; the environment supplies the
// bodies. On the QEMU `virt` board that environment is this file plus boot.S.
// `__saw_rt_write` and `__saw_rt_panic` drive the same NS16550A UART the Saw
// driver uses (0x1000_0000); `__saw_rt_panic` additionally FAILS the run
// through the SiFive test finisher. A bump allocator over a fixed .bss arena
// backs `__saw_rt_alloc` (enough for panic message assembly), and the mem*
// helpers cover the compiler's implicit calls.
//
// The reserved runtime symbols cannot be exported from Saw YET — design 113b's
// runtime-build mode lifts that, at which point these bodies can graduate to
// Saw (rider on the next SOS brief).

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

void __saw_rt_write(const char *ptr, usize len) {
    uart_write(ptr, len);
}

__attribute__((noreturn))
void __saw_rt_panic(const char *msg, usize len) {
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

void *__saw_rt_alloc(usize size, usize align) {
    if (align < 1) align = 1;
    usize p = (arena_next + (align - 1)) & ~(align - 1);
    if (p + size > ARENA_BYTES) {
        __saw_rt_panic("sos: out of arena memory\n", 25);
    }
    arena_next = p + size;
    return &arena[p];
}

void __saw_rt_dealloc(void *ptr, usize size, usize align) {
    (void)ptr; (void)size; (void)align;
}

// ---- the appended payload (design 140) ------------------------------------
//
// The root image rides after the kernel in the same ELF, in the `.payload`
// section virt.ld bounds with `_payload_start` / `_payload_end` (see the
// `.incbin` stub the harness assembles). Saw cannot name a linker symbol, so
// the bounds arrive through these two accessors. An image with no payload gets
// an empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u32 sos_payload_start(void) { return (u32)(usize)_payload_start; }
u32 sos_payload_end(void)   { return (u32)(usize)_payload_end; }

// ---- PMP: the Profile A isolation primitive (design 140) ------------------
//
// Spec §5.5: on Profile A an AddressSpace IS a PMP region set. RISC-V gives us
// default-deny for free — "if no PMP entry matches a U-mode access, and at
// least one entry is implemented, the access fails" — so the kernel, the UART
// and the test finisher are locked away from root by SAYING NOTHING about them.
// The kernel grants root exactly its own ranges. M-mode is unconstrained
// because SOS never sets an entry's L (lock) bit.
//
// A region is a TOR pair: entry 2*idx holds the lower bound with A=OFF (a bound
// only, never matched), entry 2*idx+1 holds the upper bound with A=TOR plus the
// R/W/X bits. Addresses are stored shifted right by two — PMP's 4-byte grain.
// The M1 budget is PMP_REGIONS regions / 8 of the 16 entries QEMU implements;
// the layout is recorded in virt.ld.
//
// These are the CSR half of the riscv32 HAL that M1b (design 79) extracts.

#define PMP_A_TOR    (1u << 3)
#define PMP_REGIONS  4

static u32 pmpcfg_lo;       // entries 0-3
static u32 pmpcfg_hi;       // entries 4-7

static void pmpaddr_write(u32 i, u32 v) {
    // The CSR number must be an assembly-time immediate, so this is a switch
    // rather than a computed index.
    switch (i) {
    case 0: __asm__ volatile("csrw pmpaddr0, %0" :: "r"(v)); break;
    case 1: __asm__ volatile("csrw pmpaddr1, %0" :: "r"(v)); break;
    case 2: __asm__ volatile("csrw pmpaddr2, %0" :: "r"(v)); break;
    case 3: __asm__ volatile("csrw pmpaddr3, %0" :: "r"(v)); break;
    case 4: __asm__ volatile("csrw pmpaddr4, %0" :: "r"(v)); break;
    case 5: __asm__ volatile("csrw pmpaddr5, %0" :: "r"(v)); break;
    case 6: __asm__ volatile("csrw pmpaddr6, %0" :: "r"(v)); break;
    case 7: __asm__ volatile("csrw pmpaddr7, %0" :: "r"(v)); break;
    default: break;
    }
}

// Clear every entry. Called before programming a fresh region set.
void sos_pmp_reset(void) {
    pmpcfg_lo = 0;
    pmpcfg_hi = 0;
    __asm__ volatile("csrw pmpcfg0, zero" ::: "memory");
    __asm__ volatile("csrw pmpcfg1, zero" ::: "memory");
    for (u32 i = 0; i < PMP_REGIONS * 2u; i++) {
        pmpaddr_write(i, 0);
    }
}

// Stage region `idx` as [base, top) with `perm` (bit 0 R, bit 1 W, bit 2 X).
// Out-of-budget indices are ignored here and rejected by the caller, which owns
// the diagnostic.
void sos_pmp_region(u32 idx, u32 base, u32 top, u32 perm) {
    if (idx >= PMP_REGIONS) {
        return;
    }
    u32 lo = idx * 2u;
    u32 hi = lo + 1u;
    pmpaddr_write(lo, base >> 2);
    pmpaddr_write(hi, top >> 2);
    u32 cfg = (perm & 0x7u) | PMP_A_TOR;
    if (hi < 4u) {
        pmpcfg_lo |= cfg << (8u * hi);
    } else {
        pmpcfg_hi |= cfg << (8u * (hi - 4u));
    }
}

// Publish the staged region set. Separate from `sos_pmp_region` so a partially
// programmed set is never live.
void sos_pmp_commit(void) {
    __asm__ volatile("csrw pmpcfg0, %0" :: "r"(pmpcfg_lo) : "memory");
    __asm__ volatile("csrw pmpcfg1, %0" :: "r"(pmpcfg_hi) : "memory");
}

// ---- atomic libcalls ------------------------------------------------------
//
// The Saw object is built for bare `rv32i` (llvmlite's default for the triple),
// which has no A extension, so LLVM lowers String's refcount traffic to
// `__atomic_*_4` libcalls instead of `amoadd.w`. The kernel prints, therefore
// the kernel needs them.
//
// Plain read-modify-write is CORRECT HERE and nowhere else: SOS v1 is a
// uniprocessor kernel (spec §7) and M1 enables no interrupt sources, so no
// other agent can observe the window. Two later changes each invalidate this
// and must replace these bodies: enabling interrupts (an ISR touching a
// refcount) and SMP. Building the Saw object for `rv32ia` would retire them
// outright.

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
