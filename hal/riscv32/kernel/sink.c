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

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;

// ---- the board's console and its stop button ------------------------------
//
// NOT YET DIETED — design 172 unit 4 moves both. They are the runtime seams'
// two hooks, including the panic seam's, which is why the replacement has to be
// check-free by construction rather than merely correct.

#define UART_BASE    0x10000000u
#define UART_THR     0x0u
#define UART_LSR     0x5u
#define LSR_TX_EMPTY 0x20u          // LSR bit 5 — TX holding register empty
#define SIFIVE_TEST  0x00100000u
#define FINISH_FAIL  0x3333u
#define FAIL_CODE_SHIFT 16u

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
