// SOS arm64 KERNEL HAL, native half (designs 162, 172).
//
// EVERY LINE IN THIS FILE IS C FOR ONE OF THREE REASONS, and each function
// states its own:
//
//   1. an INSTRUCTION with no Saw spelling — `hlt` with pinned registers,
//      `msr`/`mrs`, `dsb`/`isb`/`tlbi`;
//   2. a LINKER SYMBOL's ADDRESS, which Saw cannot name (DF-172a in
//      designs/todo.md: `extern` declares only functions, an extern function is
//      not usable as a value, and `@export` on a static emits a DEFINITION
//      rather than a reference);
//   3. nothing. There is no third reason — the design-172 diet removed
//      everything that had one.
//
// What used to be here and is now `lib.saw`: the PL011 write loop, the
// page-table construction, the grant editing, the kernel-fault report and its
// hex formatting. `boot.S` beside this file is the vectors and the privilege
// transitions; `lib.saw` is the Saw surface the kernel imports.
//
// `sos/hal/arm64/user/` is the process-side counterpart.

typedef unsigned long u64;
typedef unsigned int  u32;
typedef unsigned char u8;
typedef unsigned long usize;   // LP64: 64-bit, matches platform `Int`

// ---- from lib.saw ---------------------------------------------------------
//
// Building the identity map is arithmetic and 64-bit stores, so it is Saw
// (design 172 unit 1). What is left here is the ACTIVATION: four system
// registers, a barrier pair and a TLB flush. `sos_mair_value` comes across with
// the table because MAIR's bytes are the other half of the descriptor encoding
// `lib.saw` owns — a memory type added there would otherwise need a matching
// edit here, with nothing to catch a missed one.

u64 sos_page_tables_build(void);
u64 sos_mair_value(void);

// ---- stopping the machine -------------------------------------------------
//
// C BECAUSE: `hlt #0xf000` with the semihosting call number and parameter block
// pinned in x0/x1 — an instruction and a register assignment, neither of which
// Saw can spell.
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

// ---- PL011 UART -----------------------------------------------------------
//
// NOT YET DIETED — design 172 unit 4 moves this. It is the console the runtime
// seams write through, including the panic seam, which is why its replacement
// has to be check-free by construction rather than merely correct.

#define PL011_BASE   0x09000000UL
#define UARTDR       0x00
#define UARTFR       0x18
#define FR_TXFF      (1u << 5)      // transmit FIFO full

void sos_rt_write(const char *ptr, usize len) {
    volatile u32 *fr = (volatile u32 *)(PL011_BASE + UARTFR);
    volatile u32 *dr = (volatile u32 *)(PL011_BASE + UARTDR);
    for (usize i = 0; i < len; i++) {
        while (*fr & FR_TXFF) { }
        *dr = (u8)ptr[i];
    }
}

// Stop the machine, non-zero. A zero code would report success, so it is
// promoted — a failing exit never reads as a passing one.
__attribute__((noreturn))
void sos_rt_abort(u32 code) {
    u32 status = code & 0xFFu;
    if (status == 0) status = 1u;
    sos_platform_exit(status);
}

// ---- the appended payload -------------------------------------------------
//
// C BECAUSE: a linker symbol's address (reason 2 above). The root image rides
// after the kernel in the same ELF, in the `.payload` section virt.ld bounds
// with `_payload_start` / `_payload_end`. An image with no payload gets an
// empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u64 sos_payload_start(void) { return (u64)_payload_start; }
u64 sos_payload_end(void)   { return (u64)_payload_end; }

// ---- turning the MMU on ---------------------------------------------------
//
// C BECAUSE: `msr`/`mrs` name a system register at assembly time, and `dsb`/
// `isb` are barriers. The MAP itself is built in `lib.saw` and arrives here as
// an address.
//
// Called by `_start` after `.bss` is zeroed, because the tables live there.

void sos_mmu_init(void) {
    u64 ttbr0 = sos_page_tables_build();
    u64 mair = sos_mair_value();

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
    __asm__ volatile("msr ttbr0_el1, %0" :: "r"(ttbr0));
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

// ---- publishing a staged grant set ----------------------------------------
//
// C BECAUSE: `dsb`/`isb` are barriers and `tlbi` is TLB maintenance. The
// descriptor writes are `lib.saw`'s; what is needed after them is ordering and
// a flush. The table walks are cacheable and inner-shareable, so no cache
// maintenance is required on this machine.

void sos_prot_commit(void) {
    __asm__ volatile("dsb ishst; tlbi vmalle1; dsb ish; isb" ::: "memory");
}
