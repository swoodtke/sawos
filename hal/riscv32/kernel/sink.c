// SOS riscv32 KERNEL HAL (design 140) — the board and the CSRs.
//
// Everything in the kernel's runtime that knows it is riscv32 on QEMU `virt`:
// the two per-side hooks `sos/rt/common_c/support.c` calls out to, the PMP
// helpers (CSR writes need assembly-time immediates), and the linker-symbol
// accessors Saw cannot express. `boot.S` beside this file is the rest.
//
// `sos/hal/riscv32/user/` is the process-side counterpart. M1b (design 79)
// adds `sos/hal/arm64/kernel/` and `.../user/` beside these — the layout is
// chosen so that milestone ADDS directories rather than moving any.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;

#define UART_BASE    0x10000000u
#define UART_THR     0x0u
#define UART_LSR     0x5u
#define LSR_TX_EMPTY 0x20u          // LSR bit 5 — TX holding register empty
#define SIFIVE_TEST  0x00100000u
#define FINISH_FAIL  0x3333u
#define FAIL_CODE_SHIFT 16u

// ---- the two hooks the common runtime calls -------------------------------

// The kernel owns the machine, so its console is the UART directly. (The Saw
// side drives the same device through the design-112 `UnsafeMemory` driver;
// this exists because the panic seam has to work even when Saw code is what
// panicked.)
void sos_rt_write(const char *ptr, usize len) {
    volatile u8 *lsr = (volatile u8 *)(UART_BASE + UART_LSR);
    volatile u8 *thr = (volatile u8 *)(UART_BASE + UART_THR);
    for (usize i = 0; i < len; i++) {
        while ((*lsr & LSR_TX_EMPTY) == 0) { }
        *thr = (u8)ptr[i];
    }
}

// Stop the machine, non-zero. A zero code would make the finisher report
// success, so it is promoted — a failing exit never reads as a passing one.
__attribute__((noreturn))
void sos_rt_abort(u32 code) {
    u32 status = code & 0xFFu;
    if (status == 0) status = 1u;
    volatile u32 *test = (volatile u32 *)SIFIVE_TEST;
    *test = FINISH_FAIL | (status << FAIL_CODE_SHIFT);
    for (;;) { __asm__ volatile("wfi"); }
}

// ---- the appended payload -------------------------------------------------
//
// The root image rides after the kernel in the same ELF, in the `.payload`
// section virt.ld bounds with `_payload_start` / `_payload_end`. Saw cannot
// name a linker symbol, so the bounds arrive through these accessors. An image
// with no payload gets an empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u32 sos_payload_start(void) { return (u32)(usize)_payload_start; }
u32 sos_payload_end(void)   { return (u32)(usize)_payload_end; }

// ---- PMP: the Profile A isolation primitive -------------------------------
//
// Spec §5.5: on Profile A an AddressSpace IS a PMP region set. RISC-V gives us
// default-deny for free — "if no PMP entry matches a U-mode access, and at
// least one entry is implemented, the access fails" — so the kernel, the UART
// and the finisher are locked away from root by SAYING NOTHING about them. The
// kernel grants root exactly its own ranges. M-mode is unconstrained because
// SOS never sets an entry's L (lock) bit.
//
// A region is a TOR pair: entry 2*idx holds the lower bound with A=OFF (a bound
// only, never matched), entry 2*idx+1 holds the upper bound with A=TOR plus the
// R/W/X bits. Addresses are stored shifted right by two — PMP's 4-byte grain.
// The M1 budget is PMP_REGIONS regions / 8 of the 16 entries QEMU implements;
// the layout is recorded in virt.ld.

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
