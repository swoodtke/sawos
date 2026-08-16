// SOS riscv32 KERNEL HAL, native half (designs 140, 172).
//
// EVERY LINE IN THIS FILE IS C FOR ONE OF TWO REASONS, and each function states
// its own:
//
//   1. a CSR NUMBER is an assembly-time immediate — `csrw pmpaddr3, x` names
//      the register in the instruction, so a computed index has to become a
//      switch and there is no Saw spelling for any of it;
//   2. a LINKER SYMBOL's ADDRESS, which Saw cannot name (DF-172a in
//      designs/todo.md: `extern` declares only functions, an extern function is
//      not usable as a value, and `@export` on a static emits a DEFINITION
//      rather than a reference).
//
// What used to be here and is now `lib.saw`: the NS16550A write loop, the
// finisher write that stops the machine, and all of the PMP region arithmetic —
// the config-word staging, the TOR pairing and the grant budget. This file
// programs the registers it is handed and decides nothing.
//
// `boot.S` beside this file is the trap entry and the M -> U transition;
// `sos/hal/riscv32/user/` is the process-side counterpart.

typedef unsigned int u32;

// ---- the appended payload -------------------------------------------------
//
// C BECAUSE: a linker symbol's address (reason 2 above). The root image rides
// after the kernel in the same ELF, in the `.payload` section virt.ld bounds
// with `_payload_start` / `_payload_end`. An image with no payload gets an
// empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u32 sos_payload_start(void) { return (u32)(unsigned long)_payload_start; }
u32 sos_payload_end(void)   { return (u32)(unsigned long)_payload_end; }

// ---- PMP register access --------------------------------------------------
//
// C BECAUSE: the CSR number is an assembly-time immediate (reason 1 above),
// which is why an indexed write is a switch rather than a loop.
//
// What a region MEANS — the TOR pair, the config bits, which entries the board
// budget spends, what a sosimg permission mask becomes — is `lib.saw`'s
// (design 172 unit 1). These two functions place words in registers.

void sos_pmpaddr_write(u32 i, u32 v) {
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

// The two config registers covering entries 0-3 and 4-7, written together so a
// partially programmed region set is never live.
void sos_pmpcfg_write(u32 lo, u32 hi) {
    __asm__ volatile("csrw pmpcfg0, %0" :: "r"(lo) : "memory");
    __asm__ volatile("csrw pmpcfg1, %0" :: "r"(hi) : "memory");
}

// ---- which interrupt classes may reach this hart --------------------------
//
// C BECAUSE: the CSR number is an assembly-time immediate (reason 1 above).
// WHICH classes, and the shadow the mask is staged in, are `lib.saw`'s.
//
// Note what is NOT written here: the GLOBAL interrupt enable (mstatus.MIE).
// SOS never sets it, which is design 178's D2 — a machine interrupt reaches a
// lower privilege mode unconditionally and reaches M-mode only through that
// bit, so leaving it clear IS "interrupts are taken from user mode only".

void sos_mie_write(u32 mask) { __asm__ volatile("csrw mie, %0" :: "r"(mask) : "memory"); }

// ---- parking the core while the kernel idles ------------------------------
//
// C BECAUSE: `wfi` is an INSTRUCTION (reason 1 above), and there is no Saw
// spelling for one.
//
// It wakes on a pending interrupt of a locally ENABLED class regardless of the
// global enable, which is what the idle path needs: SOS never sets that bit
// (design 178 D2), so the interrupt is never TAKEN here — the core simply
// resumes at the next instruction and `irq_poll` in lib.saw asks the controller
// what arrived. A spurious wake costs one poll.

void sos_wait_for_irq(void) { __asm__ volatile("wfi"); }
