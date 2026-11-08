// SOS arm64 kernel HAL, native half.
//
// Most of this file is C for one of two reasons, and each function states its
// own:
//
//   1. an instruction with no Saw spelling: `hlt` with pinned registers,
//      `msr`/`mrs`, `dsb`/`isb`/`tlbi`;
//   2. a linker symbol's address, which Saw cannot name (`extern` declares
//      only functions, an extern function is not usable as a value, and
//      `@export` on a static emits a definition rather than a reference).
//
// The exceptions say why where they stand: the two translation-control
// constants `boot.S` reads, and `sos_mmu_tables`, a sequencing shim.
//
// `boot.S` beside this file is the vectors and the privilege transitions;
// `lib.saw` is the Saw surface the kernel imports. `hal/arm64/user/` is the
// process-side counterpart.

typedef unsigned long u64;
typedef unsigned int  u32;

// ---- from lib.saw ---------------------------------------------------------
//
// Building the tables is arithmetic and 64-bit stores, so it is Saw. What is
// left here is the activation: system registers, barriers and a TLB flush.
// `sos_mair_value` comes across with the table because MAIR's bytes are the
// other half of the descriptor encoding `lib.saw` owns; checking them here
// catches a memory type added there without a matching edit in this file.

u64 sos_page_tables_build(void);
u64 sos_linmap_build(void);
u64 sos_mair_value(void);

// ---- the linear map, and the one arithmetic fact this file needs ------------
//
// The kernel links in the high half and loads at its physical addresses
// (`virt.ld` adds the offset to the VMA and takes it back off with `AT`). So
// every linker symbol's address here is a kernel address, and the places below
// that publish an address as data have to hand back the physical one.
//
// This mask is the complement of the offset `virt.ld` and `lib.saw` both spell,
// and must equal `virt.ld`'s exported `LINMAP_PHYS_MASK`. Nothing checks this
// copy: `linmap_offset_probe` in `lib.saw` checks only `lib.saw`'s offset
// against where the linker placed the kernel.
#define SOS_LINMAP_PHYS_MASK 0x0000007FFFFFFFFFUL

// ---- the two translation-control words boot.S installs ----------------------
//
// Defined here and read by `boot.S`, so each value is spelled once. The MMU has
// to be on before any compiled code can run at its link address, so the enable
// sequence is hand-written assembly, but the values it installs live here.
// `boot.S` loads both out of `.rodata` through a masked (physical) pointer,
// which works with the MMU off because `.rodata` is loaded at that address.
//
// MAIR is the one word this file cannot own: `lib.saw` builds it from the
// `MemoryType` cases the descriptors use, and a `const` initializer cannot call
// a function. So it is copied here and checked against the original in
// `sos_mmu_tables` below, on every boot.

const u64 sos_boot_mair = 0x000000000004FF00UL;

// T0SZ = T1SZ = 25: a 39-bit VA on both halves, level 1 first, 4 KiB granule.
// EPD1 is clear, so the high half is walked. A1 = 0, so TTBR0 defines the ASID.
// IPS = 0: a 32-bit output address, which covers this board's map.
const u64 sos_boot_tcr = 25UL                   // T0SZ
                       | (1UL << 8)             // IRGN0: walks write-back cacheable
                       | (1UL << 10)            // ORGN0: likewise
                       | (3UL << 12)            // SH0: inner shareable
                       | (0UL << 14)            // TG0: 4 KiB granule
                       | (25UL << 16)           // T1SZ
                       | (0UL << 22)            // A1: TTBR0 holds the ASID
                       | (0UL << 23)            // EPD1: walk the high half
                       | (1UL << 24)            // IRGN1
                       | (1UL << 26)            // ORGN1
                       | (3UL << 28)            // SH1
                       | (2UL << 30)            // TG1: 4 KiB granule
                       | (0UL << 32);           // IPS: 32-bit output

// ---- stopping the machine -------------------------------------------------
//
// C because: `hlt #0xf000` with the semihosting call number and parameter block
// pinned in x0/x1, an instruction and a register assignment Saw cannot spell.
//
// Semihosting rather than PSCI: `-M virt` offers PSCI SYSTEM_OFF over the HVC
// conduit and the ARM semihosting SYS_EXIT call. PSCI always exits the emulator
// with status 0, and the harness asserts on exit status, so a failing kernel
// would read as a passing run; `umode_bad_calls` encodes its whole verdict in
// the number. SYS_EXIT carries a 64-bit subcode QEMU exits with, so it is the
// only mechanism shaped like the RISC-V finisher. It needs `-semihosting` on
// the QEMU command line, which `tools/sos_runner.py` passes for this target.
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

// ---- the appended payload -------------------------------------------------
//
// C because: a linker symbol's address (reason 2 above). The root image rides
// after the kernel in the same ELF, in the `.payload` section virt.ld bounds
// with `_payload_start` / `_payload_end`. An image with no payload gets an
// empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

// Masked to physical: these bounds are granted to EL0 and read by the loader as
// an image base, both physical readings, while the symbol itself is a high link
// address. See the linear-map note at the top.
u64 sos_payload_start(void) { return (u64)_payload_start & SOS_LINMAP_PHYS_MASK; }
u64 sos_payload_end(void)   { return (u64)_payload_end   & SOS_LINMAP_PHYS_MASK; }

// ---- the boot region table ------------------------------------------------
//
// C because: a linker symbol's address, the same reason and shape as the
// payload bounds. The build-emitted region table lands in the `.regions`
// section virt.ld bounds; an image built with no children gets an empty
// section and start == end, which the kernel reads as zero regions.
//
// The table's rows carry blob bases the linker resolved when it placed the
// generated stub, so no per-child symbol has to be nameable here.

extern unsigned char _region_table_start[];
extern unsigned char _region_table_end[];

// Masked to physical for the reason the payload bounds are. The table's rows
// carry link addresses too (the linker resolves each blob row's base from the
// child section's own symbol); those are normalized in
// `kernel/core/process.saw`, at the one place a row is read, through the
// arch-free `hal.virt_to_phys`, which is idempotent on rows whose bases were
// literals.
u64 sos_region_table_start(void) { return (u64)_region_table_start & SOS_LINMAP_PHYS_MASK; }
u64 sos_region_table_end(void)   { return (u64)_region_table_end   & SOS_LINMAP_PHYS_MASK; }

// ---- the real translation tables -------------------------------------------
//
// `boot.S` brings the machine up on a two-descriptor boot table, jumps into the
// high half, and calls this. The tables below are therefore built with
// translation already on, and every descriptor address goes out through
// `lib.saw`'s `phys_to_virt` on the way to memory. `boot.S` then installs what
// this hands back.
//
// C because: this is a sequencing shim between two Saw builders and one
// assembly caller. It is here rather than in `lib.saw` because the MAIR
// cross-check below reads a C `const` that `boot.S` also reads.

// A boot-time bug stop distinct from anything `lib.saw`, the kernel or root
// chooses: `sos_boot_mair` has drifted from `sos_mair_value()` in `lib.saw`, so
// the attributes the MMU came up under are not the ones the descriptors mean.
// `lib.saw`'s neighbouring codes are 66 and 67.
#define SOS_ABORT_MAIR_DRIFT 68

// Build the kernel's linear map and hand back its level-1 physical base, for
// `boot.S` to install in TTBR1_EL1 from the identity map.
u64 sos_mmu_tables(void) {
    if (sos_boot_mair != sos_mair_value()) {
        sos_platform_exit(SOS_ABORT_MAIR_DRIFT);
    }
    return sos_linmap_build();
}

// ---- publishing a staged grant set ----------------------------------------
//
// C because: `dsb`/`isb` are barriers and `tlbi` is TLB maintenance. The
// descriptor writes are `lib.saw`'s; what is needed after them is ordering and
// a flush. The table walks are cacheable and inner-shareable, so no cache
// maintenance is required on this machine.

void sos_prot_commit(void) {
    __asm__ volatile("dsb ishst; tlbi vmalle1; dsb ish; isb" ::: "memory");
}

// ---- switching a persistent domain ----------------------------------------
//
// C because: reason 1 (`msr` names a system register and `tlbi` is a
// maintenance instruction, both at assembly time). The table pool, the ASID
// choice and every descriptor are `lib.saw`'s.
//
// `sos_ttbr0_write` is the whole of a process switch on this profile: the
// table base and the ASID arrive as one word, already assembled. The `dsb
// ishst` orders any descriptor stores the caller just made ahead of the switch;
// the `isb` makes the new base visible to the instructions after it.

void sos_ttbr0_write(u64 value) {
    __asm__ volatile("dsb ishst; msr ttbr0_el1, %0; isb" :: "r"(value) : "memory");
}

// Drop every cached translation carrying this ASID. `aside1is` is the
// inner-shareable by-ASID form; the argument goes in bits 63:48, matching the
// TTBR0 layout the caller already built.
//
// The trailing `dsb ish; isb` completes the invalidation before the next
// access; a revocation that had not completed would not revoke.

void sos_tlbi_asid(u64 asid) {
    __asm__ volatile("dsb ishst; tlbi aside1is, %0; dsb ish; isb"
                     :: "r"(asid << 48) : "memory");
}

// ---- the core's physical timer --------------------------------------------
//
// C because: `mrs`/`msr` name a system register at assembly time (reason 1).
// This timer is system registers, so there is no way to reach it from Saw. One
// instruction each; the period arithmetic, the tick policy and the interrupt
// controller are `lib.saw`'s.
//
// `sos_timer_count` and `sos_timer_set_compare` are the absolute pair: the
// counter is free-running and 64 bits wide, and the compare register is a
// 64-bit deadline on the same scale, so writing a deadline in the future lowers
// the line for a fire already taken. Don't use the down-counter
// (`cntp_tval_el0`): it is 32 bits signed, so it cannot express a deadline
// more than ~34 seconds out at this machine's 62.5 MHz.

u64 sos_timer_freq(void) { u64 v; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v)); return v; }
u64 sos_timer_ctl_read(void) { u64 v; __asm__ volatile("mrs %0, cntp_ctl_el0" : "=r"(v)); return v; }
void sos_timer_ctl_write(u64 v) { __asm__ volatile("msr cntp_ctl_el0, %0" :: "r"(v)); }
u64 sos_timer_count(void) { u64 v; __asm__ volatile("mrs %0, cntpct_el0" : "=r"(v)); return v; }
void sos_timer_set_compare(u64 v) { __asm__ volatile("msr cntp_cval_el0, %0" :: "r"(v)); }

// ---- parking the core while the kernel idles ------------------------------
//
// C because: `wfi` is an instruction (reason 1).
//
// It wakes on a pending physical interrupt regardless of the exception level's
// mask, which is what the idle path needs: the kernel masks interrupts at EL1
// throughout, so the interrupt is never taken here. The core resumes at the
// next instruction and `irq_poll` in lib.saw asks the controller what arrived.
// A spurious wake costs one poll.

void sos_wait_for_irq(void) { __asm__ volatile("wfi"); }
