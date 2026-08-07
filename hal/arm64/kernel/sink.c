// SOS arm64 KERNEL HAL (design 162) — the board, the exit, and the page tables.
//
// Everything in the kernel's runtime that knows it is arm64 on QEMU `virt`: the
// two per-side hooks `sos/rt/common_c/support.c` calls out to, the static
// identity map that is this profile's memory-protection primitive, and the
// linker-symbol accessors Saw cannot express. `boot.S` beside this file is the
// rest, and `lib.saw` is the Saw surface the kernel actually imports.
//
// `sos/hal/arm64/user/` is the process-side counterpart.

typedef unsigned long  u64;
typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // LP64: 64-bit, matches platform `Int`

// ---- PL011 UART -----------------------------------------------------------

#define PL011_BASE   0x09000000UL
#define UARTDR       0x00
#define UARTFR       0x18
#define FR_TXFF      (1u << 5)      // transmit FIFO full

// The kernel owns the machine, so its console is the UART directly. (The Saw
// side drives the same device through the design-112 `UnsafeMemory` driver;
// this exists because the panic seam has to work even when Saw code is what
// panicked.)
void sos_rt_write(const char *ptr, usize len) {
    volatile u32 *fr = (volatile u32 *)(PL011_BASE + UARTFR);
    volatile u32 *dr = (volatile u32 *)(PL011_BASE + UARTDR);
    for (usize i = 0; i < len; i++) {
        while (*fr & FR_TXFF) { }
        *dr = (u8)ptr[i];
    }
}

// ---- stopping the machine -------------------------------------------------
//
// WHY SEMIHOSTING AND NOT PSCI. `-M virt` gives two ways to stop: PSCI
// SYSTEM_OFF over the HVC conduit, and the ARM semihosting SYS_EXIT call. PSCI
// always exits the emulator with status 0, and this harness asserts on exit
// STATUS — a failing kernel that exits 0 reads as a passing run, and one case
// (`umode_bad_calls`) encodes its whole verdict in the number. SYS_EXIT carries
// a 64-bit subcode QEMU exits with, so it is the only mechanism with the
// RISC-V finisher's shape. It needs `-semihosting` on the QEMU command line,
// which `tools/sos_runner.py` passes for this target.
//
// Real hardware has neither; a board build replaces this with a reset.

#define ADP_STOPPED_APPLICATION_EXIT 0x20026UL
#define SYS_EXIT 0x18

__attribute__((noreturn))
void sos_platform_exit(u64 code) {
    // The A64 form of SYS_EXIT takes a two-word parameter block; the second
    // word is the status the emulator exits with.
    volatile u64 block[2];
    block[0] = ADP_STOPPED_APPLICATION_EXIT;
    block[1] = code;
    register u64 x0 __asm__("x0") = SYS_EXIT;
    register u64 x1 __asm__("x1") = (u64)&block[0];
    __asm__ volatile("hlt #0xf000" :: "r"(x0), "r"(x1) : "memory");
    for (;;) { __asm__ volatile("wfi"); }
}

// Stop the machine, non-zero. A zero code would report success, so it is
// promoted — a failing exit never reads as a passing one.
__attribute__((noreturn))
void sos_rt_abort(u32 code) {
    u32 status = code & 0xFFu;
    if (status == 0) status = 1u;
    sos_platform_exit(status);
}

// ---- the kernel-bug path --------------------------------------------------

static void put_str(const char *s) {
    usize n = 0;
    while (s[n]) n++;
    sos_rt_write(s, n);
}

static void put_hex(u64 v) {
    char buf[19];
    buf[0] = '0';
    buf[1] = 'x';
    for (int i = 0; i < 16; i++) {
        u64 nib = (v >> ((15 - i) * 4)) & 0xF;
        buf[2 + i] = (char)(nib < 10 ? '0' + nib : 'a' + nib - 10);
    }
    buf[18] = 0;
    put_str(buf);
}

#define ESR_EC_SHIFT 26
#define ESR_EC_MASK  0x3F

// A trap the kernel itself took. Reports the exception class and stops the
// machine with it as the status, which is the arm64 spelling of the RISC-V
// finisher's "mcause in the code bits". Re-entrant by construction: a second
// fault while reporting exits immediately rather than looping on the console.
static int in_kernel_fault = 0;

__attribute__((noreturn))
void sos_kernel_fault(u64 esr) {
    u64 ec = (esr >> ESR_EC_SHIFT) & ESR_EC_MASK;
    if (in_kernel_fault) {
        sos_platform_exit(ec != 0 ? ec : 1);
    }
    in_kernel_fault = 1;
    u64 elr, far;
    __asm__ volatile("mrs %0, elr_el1" : "=r"(elr));
    __asm__ volatile("mrs %0, far_el1" : "=r"(far));
    put_str("SOS: kernel fault ec=");
    put_hex(ec);
    put_str(" elr=");
    put_hex(elr);
    put_str(" far=");
    put_hex(far);
    put_str("\n");
    sos_platform_exit(ec != 0 ? ec : 1);
}

// ---- the appended payload -------------------------------------------------

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u64 sos_payload_start(void) { return (u64)_payload_start; }
u64 sos_payload_end(void)   { return (u64)_payload_end; }

// ---- the static identity map: this profile's isolation primitive ----------
//
// Spec §5b: on Profile B an AddressSpace is a page-table root. M1b implements
// the STATIC parity of Profile A's PMP and nothing more (design 162 decision
// 2): one identity map, built once at boot, whose only mutable part is the
// EL0 permission bits of the pages a root image was granted. No dynamic
// mapping, no ASIDs, no address-space switch — Mapping objects are M2.
//
// The shape:
//
//   level 1 (1 GiB entries, 39-bit VA out of TTBR0)
//     [0]  0x0000_0000  device block, EL1 RW, never executable
//     [1]  0x4000_0000  -> level 2
//     rest             INVALID, which is what makes UNMAPPED_PROBE fault
//   level 2 (2 MiB entries over RAM)
//     [0], [1]         -> level 3 tables: the GRANT WINDOW, page-granular
//     rest             2 MiB blocks, EL1 RW, no EL0
//   level 3 (4 KiB pages over the first 4 MiB of RAM)
//     reset state      EL1 read/write/EXECUTE, no EL0 access at all
//     granted pages    EL0 RO or RW, never EL0-writable-and-executable
//
// The grant window covers the kernel image AND the root region, because BOTH
// need page granularity: the root region obviously, and the kernel image
// because the harness's unit-A cases grant a payload that executes in place
// inside it. Everything outside the window is EL1-only, so EL0 default-deny is
// the same property PMP gives for free — a page EL0 was never granted is a
// translation fault, not a permitted access.
//
// The kernel's own pages stay EL1-RWX: that is the arm64 spelling of "M-mode is
// unconstrained", not a claim about kernel W^X, which neither profile has yet.

#define PT_ENTRIES   512
#define RAM_BASE     0x40000000UL
#define GRANT_PAGES  1024              // 4 MiB reachable at page granularity
#define PAGE_SIZE    4096UL

static u64 level1[PT_ENTRIES]      __attribute__((aligned(4096)));
static u64 level2[PT_ENTRIES]      __attribute__((aligned(4096)));
static u64 level3[GRANT_PAGES]     __attribute__((aligned(4096)));

#define DESC_VALID      (1UL << 0)
#define DESC_BLOCK      (0UL << 1)
#define DESC_TABLE      (1UL << 1)
#define DESC_PAGE       (1UL << 1)     // at level 3 the same bit means "page"
#define ATTR_IDX(n)     ((u64)(n) << 2)
#define ATTR_AP_EL1_RW  (0UL << 6)     // EL1 read/write, EL0 no access
#define ATTR_AP_EL0_RW  (1UL << 6)     // EL1 + EL0 read/write
#define ATTR_AP_EL1_RO  (2UL << 6)     // EL1 read-only, EL0 no access
#define ATTR_AP_EL0_RO  (3UL << 6)     // EL1 + EL0 read-only
#define ATTR_SH_INNER   (3UL << 8)
#define ATTR_AF         (1UL << 10)
#define ATTR_PXN        (1UL << 53)    // not executable at EL1
#define ATTR_UXN        (1UL << 54)    // not executable at EL0

#define MAIR_DEVICE 0                  // Device-nGnRnE
#define MAIR_NORMAL 1                  // Normal, write-back, RW-allocate

// This HAL's own failure code, distinct from anything the kernel or root
// chooses. A grant the window cannot express is a kernel bug, not an image
// problem, so it does not travel through `fatal_image`.
#define ABORT_GRANT_OUTSIDE_WINDOW 66u

// The sosimg SegFlag vocabulary, which is what `prot_region` is handed.
#define SEG_FLAG_READ    1UL
#define SEG_FLAG_WRITE   2UL
#define SEG_FLAG_EXECUTE 4UL

// A grant-window page as the KERNEL sees it: read, write and execute at EL1,
// nothing at EL0.
static u64 kernel_page(u64 va) {
    return va | DESC_VALID | DESC_PAGE | ATTR_IDX(MAIR_NORMAL) | ATTR_AF
              | ATTR_SH_INNER | ATTR_AP_EL1_RW | ATTR_UXN;
}

void sos_mmu_init(void) {
    level1[0] = DESC_VALID | DESC_BLOCK | ATTR_IDX(MAIR_DEVICE) | ATTR_AF
              | ATTR_AP_EL1_RW | ATTR_PXN | ATTR_UXN;
    level1[1] = ((u64)&level2[0]) | DESC_VALID | DESC_TABLE;

    for (u32 i = 0; i < PT_ENTRIES; i++) {
        u64 va = RAM_BASE + ((u64)i << 21);
        level2[i] = va | DESC_VALID | DESC_BLOCK | ATTR_IDX(MAIR_NORMAL)
                  | ATTR_AF | ATTR_SH_INNER | ATTR_AP_EL1_RW | ATTR_UXN;
    }
    for (u32 t = 0; t < GRANT_PAGES / PT_ENTRIES; t++) {
        level2[t] = ((u64)&level3[t * PT_ENTRIES]) | DESC_VALID | DESC_TABLE;
    }
    for (u32 i = 0; i < GRANT_PAGES; i++) {
        level3[i] = kernel_page(RAM_BASE + ((u64)i << 12));
    }

    u64 mair = (0x00UL << (8 * MAIR_DEVICE)) | (0xFFUL << (8 * MAIR_NORMAL));
    u64 tcr = 25UL                  // T0SZ = 25: a 39-bit VA, level 1 first
            | (1UL << 8)            // IRGN0: walks are write-back cacheable
            | (1UL << 10)           // ORGN0: likewise
            | (3UL << 12)           // SH0: inner shareable
            | (0UL << 14)           // TG0: 4 KiB granule
            | (25UL << 16)          // T1SZ, unused but not left reserved
            | (1UL << 23)           // EPD1: no TTBR1 walks at all
            | (2UL << 30)           // TG1: 4 KiB granule
            | (0UL << 32);          // IPS: 32-bit output, which covers the map

    __asm__ volatile("msr mair_el1, %0" :: "r"(mair));
    __asm__ volatile("msr tcr_el1, %0" :: "r"(tcr));
    __asm__ volatile("msr ttbr0_el1, %0" :: "r"((u64)&level1[0]));
    __asm__ volatile("dsb ish; isb" ::: "memory");

    // Read-modify-write rather than a constant: the reset value carries the
    // stack-alignment and endianness bits this kernel has no opinion about.
    u64 sctlr;
    __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
    sctlr |= (1UL << 0)     // M: enable the MMU
           | (1UL << 2)     // C: data cache
           | (1UL << 12);   // I: instruction cache
    __asm__ volatile("msr sctlr_el1, %0" :: "r"(sctlr));
    __asm__ volatile("isb" ::: "memory");
}

// Revoke every grant: the whole window goes back to kernel-only.
void sos_prot_reset(void) {
    for (u32 i = 0; i < GRANT_PAGES; i++) {
        level3[i] = kernel_page(RAM_BASE + ((u64)i << 12));
    }
}

// Stage grant `idx` as [base, top) with `perms` in the SegFlag vocabulary.
//
// `idx` is unused here: PMP has a fixed number of numbered regions and this
// profile has pages, so the index is a Profile-A budget concept the seam
// carries for both. A page outside the window is IGNORED rather than mapped
// somewhere else — the loader has already refused any address outside root's
// region, and silently widening the map is the one thing a protection
// primitive must never do.
//
// A grant installs exactly the permissions the image asked for — the same
// contract Profile A's PMP has, so the two profiles cannot disagree about what
// an image means. (W^X is the linker script's property and `has_sane_perms`'s
// validation; silently dropping a bit the image asked for would be the kind of
// quiet no-op that hides a bad image rather than refusing it.) PXN is the one
// thing added unconditionally: the kernel must not execute user memory even by
// accident, which is a guarantee Profile A's unconstrained M-mode cannot make.
//
// A page OUTSIDE the grant window stops the machine rather than being skipped.
// Skipping would be safe in the deny direction — EL0 simply would not reach it —
// but it would mean the kernel silently granted less than it was asked for, and
// the symptom would be a user-mode fault at some unrelated later address. The
// only way to get here is a memory map that outgrew the window, which is a
// kernel bug and reads as one.
void sos_prot_region(u64 idx, u64 base, u64 top, u64 perms) {
    (void)idx;
    u64 attrs = DESC_VALID | DESC_PAGE | ATTR_IDX(MAIR_NORMAL) | ATTR_AF
              | ATTR_SH_INNER | ATTR_PXN;
    attrs |= (perms & SEG_FLAG_WRITE) ? ATTR_AP_EL0_RW : ATTR_AP_EL0_RO;
    if (!(perms & SEG_FLAG_EXECUTE)) {
        attrs |= ATTR_UXN;
    }
    for (u64 va = base & ~(PAGE_SIZE - 1); va < top; va += PAGE_SIZE) {
        u64 page = (va - RAM_BASE) >> 12;
        if (va < RAM_BASE || page >= GRANT_PAGES) {
            put_str("SOS: grant outside the page-granular window: ");
            put_hex(va);
            put_str("\n");
            sos_platform_exit(ABORT_GRANT_OUTSIDE_WINDOW);
        }
        level3[page] = va | attrs;
    }
}

// Publish the staged set. The table walks are cacheable and inner-shareable, so
// the writes above are visible to the walker without cache maintenance; what is
// needed is ordering and a TLB flush.
void sos_prot_commit(void) {
    __asm__ volatile("dsb ishst; tlbi vmalle1; dsb ish; isb" ::: "memory");
}
