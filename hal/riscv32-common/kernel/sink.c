// SOS riscv32 kernel HAL, native half, shared by every riscv32 board.
//
// It names CSRs, one instruction and two linker symbol pairs and nothing about
// a board, so both boards' build entries point at this one file. If a board
// ever needs a different leaf, it declares that leaf itself (the `extern` block
// in each board's `lib.saw` is already split by caller for that reason).
//
// Every line in this file is C for one of two reasons, and each function
// states its own:
//
//   1. an instruction with no Saw spelling: `wfi`, and every CSR access,
//      since a CSR number is an assembly-time immediate (`csrw pmpaddr3, x`
//      names the register in the instruction, so a computed index has to
//      become a switch);
//   2. a linker symbol's address, which Saw cannot name (`extern` declares only
//      functions, an extern function is not usable as a value, and `@export` on
//      a static emits a definition rather than a reference).
//
// The console write loop and the stop are Saw in the board's `lib.saw`, and all
// of the PMP region arithmetic (the config-word staging, the TOR pairing and
// the grant budget) is Saw in the shared `rv32core` module beside this file.
// This file programs the registers it is handed and decides nothing.
//
// The shared `trap.S` is the trap entry and the M -> U transition, each
// board's `boot.S` is its reset entry, and each board's linker script bounds
// the two symbol pairs below. `../user/syscall.c` is the process-side
// counterpart, shared the same way.

typedef unsigned int u32;

// ---- the appended payload -------------------------------------------------
//
// C because: a linker symbol's address (reason 2 above). The root image rides
// after the kernel in the same ELF, in the `.payload` section the board's own
// linker script bounds with `_payload_start` / `_payload_end`. An image with no
// payload gets an empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u32 sos_payload_start(void) { return (u32)(unsigned long)_payload_start; }
u32 sos_payload_end(void)   { return (u32)(unsigned long)_payload_end; }

// ---- the boot region table ------------------------------------------------
//
// C because: a linker symbol's address (reason 2 above), the same reason and
// shape as the payload bounds. The build-emitted region table lands in the
// `.regions` section the board's own linker script bounds; an image built with
// no children gets an empty section and start == end, which the kernel reads
// as zero regions.
//
// The table's rows carry blob bases the linker resolved when it placed the
// generated stub, so no per-child symbol has to be nameable here.

extern unsigned char _region_table_start[];
extern unsigned char _region_table_end[];

u32 sos_region_table_start(void) { return (u32)(unsigned long)_region_table_start; }
u32 sos_region_table_end(void)   { return (u32)(unsigned long)_region_table_end; }

// ---- PMP register access --------------------------------------------------
//
// C because: the CSR number is an assembly-time immediate (reason 1 above),
// which is why an indexed write is a switch rather than a loop.
//
// What a region means (the TOR pair, the config bits, which entries the board
// budget spends, what a sosimg permission mask becomes) is `rv32core`'s. These
// two functions place words in registers.

// The switch covers all sixteen entries the part implements, so the budget
// above it is `rv32core`'s decision alone.
void sos_pmpaddr_write(u32 i, u32 v) {
    switch (i) {
    case 0:  __asm__ volatile("csrw pmpaddr0, %0"  :: "r"(v)); break;
    case 1:  __asm__ volatile("csrw pmpaddr1, %0"  :: "r"(v)); break;
    case 2:  __asm__ volatile("csrw pmpaddr2, %0"  :: "r"(v)); break;
    case 3:  __asm__ volatile("csrw pmpaddr3, %0"  :: "r"(v)); break;
    case 4:  __asm__ volatile("csrw pmpaddr4, %0"  :: "r"(v)); break;
    case 5:  __asm__ volatile("csrw pmpaddr5, %0"  :: "r"(v)); break;
    case 6:  __asm__ volatile("csrw pmpaddr6, %0"  :: "r"(v)); break;
    case 7:  __asm__ volatile("csrw pmpaddr7, %0"  :: "r"(v)); break;
    case 8:  __asm__ volatile("csrw pmpaddr8, %0"  :: "r"(v)); break;
    case 9:  __asm__ volatile("csrw pmpaddr9, %0"  :: "r"(v)); break;
    case 10: __asm__ volatile("csrw pmpaddr10, %0" :: "r"(v)); break;
    case 11: __asm__ volatile("csrw pmpaddr11, %0" :: "r"(v)); break;
    case 12: __asm__ volatile("csrw pmpaddr12, %0" :: "r"(v)); break;
    case 13: __asm__ volatile("csrw pmpaddr13, %0" :: "r"(v)); break;
    case 14: __asm__ volatile("csrw pmpaddr14, %0" :: "r"(v)); break;
    case 15: __asm__ volatile("csrw pmpaddr15, %0" :: "r"(v)); break;
    default: break;
    }
}

// The four config registers covering entries 0-3, 4-7, 8-11 and 12-15, written
// together so a partially programmed region set is never live. On RV32 each
// holds four entries' config bytes; RV64 packs eight per register and numbers
// them evenly, so this file is per-profile.
void sos_pmpcfg_write(u32 w0, u32 w1, u32 w2, u32 w3) {
    __asm__ volatile("csrw pmpcfg0, %0" :: "r"(w0) : "memory");
    __asm__ volatile("csrw pmpcfg1, %0" :: "r"(w1) : "memory");
    __asm__ volatile("csrw pmpcfg2, %0" :: "r"(w2) : "memory");
    __asm__ volatile("csrw pmpcfg3, %0" :: "r"(w3) : "memory");
}

// ---- which interrupt classes may reach this hart --------------------------
//
// C because: the CSR number is an assembly-time immediate (reason 1 above).
// Which classes, and the shadow the mask is staged in, are the board's, so
// this leaf is declared in each board's own `extern` block rather than in
// `rv32core`'s.
//
// This does not write the global interrupt enable (mstatus.MIE). SOS never sets
// it: a machine interrupt reaches a lower privilege mode unconditionally and
// reaches M-mode only through that bit, so leaving it clear means interrupts
// are taken from user mode only.

void sos_mie_write(u32 mask) { __asm__ volatile("csrw mie, %0" :: "r"(mask) : "memory"); }

// ---- parking the core while the kernel idles ------------------------------
//
// C because: `wfi` is an instruction (reason 1 above).
//
// It wakes on a pending interrupt of a locally enabled class regardless of the
// global enable, which is what the idle path needs: SOS never sets that bit, so
// the interrupt is never taken here. The core resumes at the next instruction
// and the board's `irq_poll` asks the controller what arrived. A spurious wake
// costs one poll.
//
// Not every board calls it. The virt board's `wait_for_irq` does. The
// ESP32-C3's is deliberately empty (its emulator's interrupt matrix never
// raises `mip`, so `wfi` would sleep forever), so that board declares no
// `extern` for this leaf and `--gc-sections` drops it.

void sos_wait_for_irq(void) { __asm__ volatile("wfi"); }
