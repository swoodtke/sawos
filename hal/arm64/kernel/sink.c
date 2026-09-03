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
// `hal/arm64/user/` is the process-side counterpart.

typedef unsigned long u64;
typedef unsigned int  u32;

// ---- from lib.saw ---------------------------------------------------------
//
// Building the identity map is arithmetic and 64-bit stores, so it is Saw
// (design 172 unit 1). What is left here is the ACTIVATION: four system
// registers, a barrier pair and a TLB flush. `sos_mair_value` comes across with
// the table because MAIR's bytes are the other half of the descriptor encoding
// `lib.saw` owns — a memory type added there would otherwise need a matching
// edit here, with nothing to catch a missed one.

u64 sos_page_tables_build(void);
u64 sos_linmap_build(void);
u64 sos_mair_value(void);

// ---- the linear map, and the one arithmetic fact this file needs ------------
//
// The kernel LINKS in the high half and LOADS at its physical addresses (sawos
// design 29; `virt.ld` adds the offset to the VMA and takes it back off with
// `AT`). So every linker symbol's address here is a KERNEL address, and the
// three places below that publish an address as DATA have to hand back the
// physical one.
//
// The offset is `virt.ld`'s number and `lib.saw`'s; this is a third spelling of
// its complement, and it is kept honest at run time rather than at build time —
// `linmap_offset_probe` in `lib.saw` compares the linker's answer against its
// own before anything can depend on either. There is no build-time comparison
// to make: a Saw `static`'s value is not a linker symbol.
#define SOS_LINMAP_PHYS_MASK 0x0000007FFFFFFFFFUL

// ---- the two translation-control words boot.S installs ----------------------
//
// **DEFINED HERE AND READ BY `boot.S`, rather than spelled twice.** The MMU has
// to be on before any compiled code can run at its link address, so the enable
// sequence is hand-written assembly — but the VALUES it installs are decisions,
// and a decision spelled in two files is a decision that drifts. `boot.S` loads
// both of these out of `.rodata` through a masked (physical) pointer, which
// works with the MMU off because `.rodata` is loaded at that address.
//
// MAIR is the one word this file cannot own: `lib.saw` builds it from the
// `MemoryType` cases the descriptors use, and a `const` initializer cannot call
// a function. So it is COPIED here and CHECKED against the original in
// `sos_mmu_tables` below, on every boot.

const u64 sos_boot_mair = 0x000000000004FF00UL;

// T0SZ = T1SZ = 25: a 39-bit VA on BOTH halves, level 1 first, 4 KiB granule.
// EPD1 is CLEAR since design 29 — the high half is walked, which is where the
// kernel lives. A1 = 0, so TTBR0 still defines the ASID (design 27 relies on
// it). IPS = 0: a 32-bit output address, which covers this board's map.
const u64 sos_boot_tcr = 25UL                   // T0SZ
                       | (1UL << 8)             // IRGN0: walks write-back cacheable
                       | (1UL << 10)            // ORGN0: likewise
                       | (3UL << 12)            // SH0: inner shareable
                       | (0UL << 14)            // TG0: 4 KiB granule
                       | (25UL << 16)           // T1SZ
                       | (0UL << 22)            // A1: TTBR0 holds the ASID
                       | (0UL << 23)            // EPD1: WALK the high half
                       | (1UL << 24)            // IRGN1
                       | (1UL << 26)            // ORGN1
                       | (3UL << 28)            // SH1
                       | (2UL << 30)            // TG1: 4 KiB granule
                       | (0UL << 32);           // IPS: 32-bit output

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

// ---- the appended payload -------------------------------------------------
//
// C BECAUSE: a linker symbol's address (reason 2 above). The root image rides
// after the kernel in the same ELF, in the `.payload` section virt.ld bounds
// with `_payload_start` / `_payload_end`. An image with no payload gets an
// empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

// MASKED TO PHYSICAL (sawos design 29). These bounds are granted to EL0 and
// read by the loader as an image base — both physical readings — while the
// symbol itself is a high link address. See the linear-map note at the top.
u64 sos_payload_start(void) { return (u64)_payload_start & SOS_LINMAP_PHYS_MASK; }
u64 sos_payload_end(void)   { return (u64)_payload_end   & SOS_LINMAP_PHYS_MASK; }

// ---- the boot region table ------------------------------------------------
//
// C BECAUSE: a linker symbol's address — the SAME reason and the same shape as
// the payload bounds. The build-emitted region table (sawos design 2 D-2) lands
// in the `.regions` section virt.ld bounds; an image built with no children
// gets an empty section and start == end, which the kernel reads as ZERO
// REGIONS.
//
// Only ONE new symbol pair, and that is a property of the format rather than of
// this file: the table's rows carry blob BASES the linker resolved when it
// placed the generated stub, so no per-child symbol has to be nameable here.

extern unsigned char _region_table_start[];
extern unsigned char _region_table_end[];

// MASKED TO PHYSICAL for the reason the payload bounds are (sawos design 29).
// The table's ROWS carry link addresses too — the linker resolves each blob
// row's base from the child section's own symbol — and those are normalized in
// `kernel/core/process.saw`, at the one place a row is read, because the mask
// there is the arch-free `hal.virt_to_phys` and is idempotent on the rows whose
// bases were literals all along.
u64 sos_region_table_start(void) { return (u64)_region_table_start & SOS_LINMAP_PHYS_MASK; }
u64 sos_region_table_end(void)   { return (u64)_region_table_end   & SOS_LINMAP_PHYS_MASK; }

// ---- the real translation tables (sawos design 29) -------------------------
//
// **TURNING THE MMU ON MOVED TO `boot.S`, AND THIS IS WHAT IS LEFT.** It used to
// be one function here: build the identity map with translation OFF, then set
// four system registers and enable. That order is no longer available. The
// kernel LINKS in the high half, so no compiled code — C or Saw — may run until
// the MMU is on and the high half is walked; the enable has to happen before the
// first call, out of hand-written assembly, against a table hand-written
// assembly can build.
//
// So `boot.S` brings the machine up on a two-descriptor BOOT table, jumps into
// the high half, and calls this. Which means the tables below are built with
// TRANSLATION ALREADY ON, and every descriptor address goes out through
// `lib.saw`'s `phys_to_virt` on the way to memory. `boot.S` then installs what
// this hands back.
//
// C BECAUSE: reason 2 does not apply and reason 1 barely does — this is a
// sequencing shim between two Saw builders and one assembly caller, and it is
// here rather than in `lib.saw` only because the MAIR cross-check below reads a
// C `const` that `boot.S` also reads.

// A boot-time bug stop distinct from anything `lib.saw`, the kernel or root
// chooses: `sos_boot_mair` above has drifted from `sos_mair_value()` in
// `lib.saw`, so the attributes the MMU came up under are not the ones the
// descriptors mean. `lib.saw`'s neighbouring codes are 66 and 67.
#define SOS_ABORT_MAIR_DRIFT 68

// Build the kernel's LINEAR MAP and hand back its level-1 physical base, for
// `boot.S` to install in TTBR1_EL1 from the identity map.
u64 sos_mmu_tables(void) {
    if (sos_boot_mair != sos_mair_value()) {
        sos_platform_exit(SOS_ABORT_MAIR_DRIFT);
    }
    return sos_linmap_build();
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

// ---- switching a persistent domain ----------------------------------------
//
// C BECAUSE: reason 1 — `msr` names a system register and `tlbi` is a
// maintenance instruction, both at assembly time. The table pool, the ASID
// choice and every descriptor are `lib.saw`'s (sawos design 27).
//
// `sos_ttbr0_write` is the whole of a process switch on this profile: the
// table base and the ASID arrive as one word, already assembled. The `dsb
// ishst` orders any descriptor stores the caller just made ahead of the switch;
// the `isb` is what makes the new base visible to the instructions after it.

void sos_ttbr0_write(u64 value) {
    __asm__ volatile("dsb ishst; msr ttbr0_el1, %0; isb" :: "r"(value) : "memory");
}

// Drop every cached translation carrying this ASID. `aside1is` is the
// inner-shareable by-ASID form; the argument goes in bits 63:48, matching the
// TTBR0 layout the caller already built.
//
// The trailing `dsb ish; isb` is what makes the invalidation complete before
// the next access — a revocation that had not landed yet would be a revocation
// that did not revoke.

void sos_tlbi_asid(u64 asid) {
    __asm__ volatile("dsb ishst; tlbi aside1is, %0; dsb ish; isb"
                     :: "r"(asid << 48) : "memory");
}

// ---- the core's physical timer --------------------------------------------
//
// C BECAUSE: `mrs`/`msr` name a system register at assembly time (reason 1
// above). Unlike Profile A's memory-mapped comparator, this timer IS system
// registers, so there is no way to reach it from Saw. One instruction each; the
// period arithmetic, the tick policy and the interrupt controller are
// `lib.saw`'s (design 178 M2 unit 1).

// One instruction each, so one line each — the same shape the payload bounds
// above have, for the same reason.
//
// `sos_timer_count` and `sos_timer_set_compare` are the ABSOLUTE pair (design
// 232 unit 1): the counter is free-running and 64 bits wide, and the compare
// register is a 64-bit deadline on the same scale, so writing a deadline in the
// future is what lowers the line for a fire already taken. The DOWN-counter
// (`cntp_tval_el0`) that M2 used instead is gone with the periodic-only seam it
// belonged to — it is 32 bits signed, so it could not express a deadline more
// than ~34 seconds out at this machine's 62.5 MHz, which a Timer object can.

u64 sos_timer_freq(void) { u64 v; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v)); return v; }
u64 sos_timer_ctl_read(void) { u64 v; __asm__ volatile("mrs %0, cntp_ctl_el0" : "=r"(v)); return v; }
void sos_timer_ctl_write(u64 v) { __asm__ volatile("msr cntp_ctl_el0, %0" :: "r"(v)); }
u64 sos_timer_count(void) { u64 v; __asm__ volatile("mrs %0, cntpct_el0" : "=r"(v)); return v; }
void sos_timer_set_compare(u64 v) { __asm__ volatile("msr cntp_cval_el0, %0" :: "r"(v)); }

// ---- parking the core while the kernel idles ------------------------------
//
// C BECAUSE: `wfi` is an INSTRUCTION (reason 1 above).
//
// It wakes on a pending physical interrupt regardless of the exception level's
// mask, which is what the idle path needs: SOS masks them at EL1 throughout
// (design 178 D2), so the interrupt is never TAKEN here — the core simply
// resumes at the next instruction and `irq_poll` in lib.saw asks the controller
// what arrived. A spurious wake costs one poll.

void sos_wait_for_irq(void) { __asm__ volatile("wfi"); }
