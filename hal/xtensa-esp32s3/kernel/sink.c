// SOS Xtensa kernel HAL, native half: ESP32-S3 board, call0.
//
// Each function states why it is C. All but one are C for one of the two
// reasons spec §5c allows:
//
//   1. an instruction with no Saw spelling: every special-register access
//      (`rsr`/`wsr` name the register in the instruction) and `simcall`;
//   2. a linker symbol's address, which Saw cannot name.
//
// The exception is `kernel_fault`, which is C for neither: it reports a trap
// the kernel took with nothing compiled from Saw beneath it, so the report
// survives a broken Saw side. Spec §5c's list does not cover it.
//
// The interrupt matrix, SYSTIMER and the UART are memory-mapped and driven
// from Saw in `lib.saw`; this file only places words in special registers and
// decides nothing. `boot.S` is the reset path, the vectors and the trap entry.
//
// Built call0 with no FPU (`-windowed`, `-fp`), as every image on this board
// is, so nothing here may reach a libgcc or ROM helper: those are windowed.

typedef unsigned int u32;

// ---- the appended payload -------------------------------------------------
//
// C because: a linker symbol's address (reason 2). The root image or a
// hand-assembled user payload rides in the `.payload` section `esp32s3.ld`
// bounds with `_payload_start` / `_payload_end`; an image with no payload
// gets an empty section and start == end.

extern unsigned char _payload_start[];
extern unsigned char _payload_end[];

u32 sos_payload_start(void) { return (u32)(unsigned long)_payload_start; }
u32 sos_payload_end(void)   { return (u32)(unsigned long)_payload_end; }

// ---- the boot region table ------------------------------------------------
//
// C because: a linker symbol's address (reason 2), the payload's shape. An
// image built with no children gets an empty `.regions` section, which the
// kernel reads as zero regions.

extern unsigned char _region_table_start[];
extern unsigned char _region_table_end[];

u32 sos_region_table_start(void) {
    return (u32)(unsigned long)_region_table_start;
}
u32 sos_region_table_end(void) {
    return (u32)(unsigned long)_region_table_end;
}

// ---- the core's interrupt registers ---------------------------------------
//
// C because: special registers (reason 1). INTENABLE gates each CPU interrupt
// at the core; INTERRUPT shows each one pending whether or not it is enabled
// (../ABI.md §6b), which is what the idle poll and the trap-time claim read;
// INTCLEAR drops an edge-triggered or software interrupt's latch.
//
// None of these touches PS. The kernel runs at INTLEVEL 15 and user mode at
// 0, so an enabled interrupt is only ever taken from user mode.

void sos_intenable_write(u32 mask) {
    __asm__ volatile("wsr %0, intenable\n rsync" :: "a"(mask) : "memory");
}

u32 sos_interrupt_read(void) {
    u32 v;
    __asm__ volatile("rsr %0, interrupt" : "=a"(v));
    return v;
}

void sos_intclear_write(u32 mask) {
    __asm__ volatile("wsr %0, intclear\n rsync" :: "a"(mask) : "memory");
}

// ---- stopping the emulator ------------------------------------------------
//
// C because: `simcall` is an instruction, and semihosting reads its call
// number and argument from fixed registers (reason 1). Under QEMU's
// `-semihosting`, SYS_exit (a2 = 1) ends the emulator with the status in a3;
// without it QEMU logs the call and returns, so every caller follows this with
// a halt of its own (../ABI.md §10).

void sos_sim_exit(u32 status) {
    register u32 a2 __asm__("a2") = 1;
    register u32 a3 __asm__("a3") = status;
    __asm__ volatile("simcall" : "+a"(a2) : "a"(a3) : "memory");
}

// ---- a trap the kernel took -----------------------------------------------
//
// C because: it reads the cause registers' values as `boot.S` captured them
// and writes the console with no Saw underneath it, so a kernel fault is
// reported even when the Saw side is what broke. `boot.S`'s `vec_fatal` calls
// it on a fresh kernel stack with the vector offset, EXCCAUSE, EPC1, EXCVADDR
// and DEPC. It prints one line and stops the machine with a failing status:
// 0x80 plus the vector's slot number, which is non-zero for every vector and
// fits the eight bits a host exit status keeps.

#define UART0_FIFO  ((volatile u32 *)0x60000000u)

static void fault_str(const char *s) {
    while (*s) {
        *UART0_FIFO = (unsigned char)*s++;
    }
}

static void fault_hex(u32 v) {
    for (int shift = 28; shift >= 0; shift -= 4) {
        u32 d = (v >> shift) & 0xF;
        *UART0_FIFO = d < 10 ? '0' + d : 'a' + d - 10;
    }
}

void kernel_fault(u32 vector, u32 cause, u32 epc1, u32 vaddr, u32 depc)
    __attribute__((noreturn));

void kernel_fault(u32 vector, u32 cause, u32 epc1, u32 vaddr, u32 depc) {
    fault_str("SOS-S3: kernel fault vector=0x");
    fault_hex(vector);
    fault_str(" exccause=0x");
    fault_hex(cause);
    fault_str(" epc1=0x");
    fault_hex(epc1);
    fault_str(" excvaddr=0x");
    fault_hex(vaddr);
    fault_str(" depc=0x");
    fault_hex(depc);
    fault_str("\n");
    sos_sim_exit(0x80 | (vector >> 6));
    for (;;) {
    }
}
