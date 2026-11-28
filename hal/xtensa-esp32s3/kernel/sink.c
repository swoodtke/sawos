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
// Built call0 with no FPU (`-windowed`, `-fp`), as the whole kernel is, so
// nothing here may reach a libgcc or ROM helper: those are windowed.

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

// ---- the kernel's own zeroed memory ---------------------------------------
//
// C because: a linker symbol's address (reason 2). `[_sos_slabs_start,
// _sos_slabs_end)` is the `.bss.sos_slab*` input sections, where the build
// links an image's slab areas; no other static of the kernel lies in it.

extern unsigned char _sos_slabs_start[];
extern unsigned char _sos_slabs_end[];

u32 sos_kernel_slabs_start(void) {
    return (u32)(unsigned long)_sos_slabs_start;
}
u32 sos_kernel_slabs_end(void) {
    return (u32)(unsigned long)_sos_slabs_end;
}

// ---- the kernel stack -----------------------------------------------------
//
// C because: a linker symbol's address (reason 2), and the stack pointer
// (reason 1). `[_stack_bottom, _stack_top)` is the stack the boot shim loads
// and every trap reloads. `sos_stack_pointer` is a leaf that reads `a1` as it
// stands inside it, so a caller learns an address below its own frame.

extern unsigned char _stack_bottom[];
extern unsigned char _stack_top[];

u32 sos_kernel_stack_bottom(void) {
    return (u32)(unsigned long)_stack_bottom;
}
u32 sos_kernel_stack_top(void) {
    return (u32)(unsigned long)_stack_top;
}

u32 sos_stack_pointer(void) {
    u32 sp;
    __asm__ volatile("mov %0, a1" : "=a"(sp));
    return sp;
}

// ---- the core's interrupt registers ---------------------------------------
//
// C because: special registers (reason 1). INTENABLE gates each CPU interrupt
// at the core; INTERRUPT shows each one pending whether or not it is enabled
// (../ABI.md §6b), which is what the idle poll and the trap-time claim read;
// INTCLEAR drops an edge-triggered or software interrupt's latch.
//
// None of these touches PS. The kernel runs at INTLEVEL 15 and user mode at
// 0, so an enabled interrupt is taken from user mode, or at the idle `waiti`
// (`boot.S`'s `sos_idle_wait`).

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

// ---- the idle wake count --------------------------------------------------
//
// C because: a linker symbol (reason 2). `boot.S`'s kernel vector adds one to
// `sos_idle_wakes` for each interrupt the idle `waiti` takes.

extern u32 sos_idle_wakes;

u32 sos_idle_wake_count(void) {
    return *(volatile u32 *)&sos_idle_wakes;
}

// ---- the cycle counter ----------------------------------------------------
//
// C because: a special register (reason 1). CCOUNT advances once per CPU
// cycle on a part; the emulator runs it at 40 MHz of host time whatever the
// clock registers say (../ABI.md §6a). `clock_init` measures the CPU's speed
// with it against SYSTIMER.

u32 sos_ccount_read(void) {
    u32 v;
    __asm__ volatile("rsr %0, ccount" : "=a"(v));
    return v;
}

// ---- stopping the emulator ------------------------------------------------
//
// C because: `simcall` is an instruction, and semihosting reads its call
// number and argument from fixed registers (reason 1). Under QEMU's
// `-semihosting`, SYS_exit (a2 = 1) ends the emulator with the status in a3;
// without it QEMU logs the call and returns, so every caller follows this with
// a halt of its own (../ABI.md §10).
//
// A board has no debugger to answer `simcall`, and what the instruction does
// there is not established, so a board image never executes it: the image is
// linked with `_sos_semihosting` at 0 (`esp32s3.ld` provides 1, the
// emulator's value) and this returns at once, leaving the caller's halt. The
// symbol is a linker constant read as an address, reason 2 above. It is weak
// so the compiler cannot assume its address is non-zero.

extern unsigned char _sos_semihosting[] __attribute__((weak));

void sos_sim_exit(u32 status) {
    if ((u32)(unsigned long)_sos_semihosting == 0) {
        return;
    }
    register u32 a2 __asm__("a2") = 1;
    register u32 a3 __asm__("a3") = status;
    __asm__ volatile("simcall" : "+a"(a2) : "a"(a3) : "memory");
}

// Whether this image is a board image, linked for the part: 1 when
// `_sos_semihosting` is 0, as a board image is linked, and 0 for an image
// linked for the emulator. The same linker constant read the same way, for
// what differs between the two machines beyond the stop (`lib.saw`'s
// `board_image`).
u32 sos_board_image(void) {
    return (u32)(unsigned long)_sos_semihosting == 0;
}

// ---- a trap the kernel took -----------------------------------------------
//
// C because: it reads the cause registers' values as `boot.S` captured them
// and writes the console with no Saw underneath it, so a kernel fault is
// reported even when the Saw side is what broke. `boot.S`'s `vec_fatal` calls
// it on a fresh kernel stack with the vector offset, EXCCAUSE, EPC1, EXCVADDR
// and DEPC. It prints one line and stops the machine with a failing status:
// 0x80 plus the vector's slot number, which is non-zero for every vector and
// fits the eight bits a host exit status keeps. On a board image the status
// goes nowhere and the line is the whole report: the machine then spins,
// with the watchdogs off, so the line stays the last thing on the console.
//
// Each byte waits for room in UART0's transmit FIFO first, as `lib.saw`'s
// console does, because a fault usually lands right after a burst of console
// lines that filled it, and a store to a full FIFO is dropped. The wait is
// bounded so that a UART which never drains cannot keep the report from
// ending in the halt. A byte leaves the FIFO every ten bit times, 87 us at
// the ROM's 115200 baud; a poll costs at least one CPU cycle, and the CPU
// never runs faster than 240 MHz, so `FAULT_POLLS_PER_BYTE` polls last at
// least 273 us, three byte times. A draining UART therefore always makes
// room within the bound, and a dead one costs the bound once per byte before
// the byte is stored anyway. The emulator reports an empty FIFO every time
// (../ABI.md §4), so there the wait ends at the first poll.
//
// A console claim is ended after the line, not before it: the stop that ends
// it is Saw, and the line must not wait on it. `sos_fault_console_stop`
// (`lib.saw`) bounds the console's writes as this file's are, then has
// `kcore` disable the console's interrupt and write what it held back while
// the claim stood, so a claimant's stored bytes, then this line, then that
// count are the run's last output, and a UART that never drains still cannot
// keep the report from its halt.

#define UART0_FIFO              ((volatile u32 *)0x60000000u)
#define UART0_STATUS            ((volatile u32 *)0x6000001Cu)
#define UART_TXFIFO_CNT_SHIFT   16
#define UART_TXFIFO_CNT_MASK    0x3FFu
#define UART_TX_FIFO_DEPTH      128u
#define FAULT_POLLS_PER_BYTE    (1u << 16)

static void fault_byte(u32 b) {
    for (u32 polls = 0; polls < FAULT_POLLS_PER_BYTE; polls++) {
        u32 cnt = (*UART0_STATUS >> UART_TXFIFO_CNT_SHIFT)
                  & UART_TXFIFO_CNT_MASK;
        if (cnt < UART_TX_FIFO_DEPTH) {
            break;
        }
    }
    *UART0_FIFO = b;
}

static void fault_str(const char *s) {
    while (*s) {
        fault_byte((unsigned char)*s++);
    }
}

static void fault_hex(u32 v) {
    for (int shift = 28; shift >= 0; shift -= 4) {
        u32 d = (v >> shift) & 0xF;
        fault_byte(d < 10 ? '0' + d : 'a' + d - 10);
    }
}

void sos_fault_console_stop(void);

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
    sos_fault_console_stop();
    sos_sim_exit(0x80 | (vector >> 6));
    for (;;) {
    }
}
