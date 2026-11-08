// SOS common runtime support: the two things Saw cannot supply itself.
//
// 1. The arena's extent. Saw cannot take a linker symbol's address, so the
//    two accessors at the foot of this file read the arena bounds that the
//    linker scripts define.
//
// 2. The 32-bit word atomics. The language assigns the atomic routines to the
//    environment on a target whose word compare-and-swap is no instruction
//    (riscv32 without the A extension, the ESP32-C3). Elsewhere the compiler
//    inlines atomic instructions, nothing references these, and
//    `--gc-sections` drops them.
//
// Both sides link this file (the kernel through `_build_shared`, every process
// image through its manifest's `[sos.<triple>] native`).

typedef unsigned int   u32;
typedef unsigned long  usize;   // matches platform `Int` (ilp32 and LP64)

// ---- word atomics ---------------------------------------------------------
//
// Plain read-modify-write. It is correct only while no involuntary switch or
// interrupt handler can touch the same word between the read and the write,
// and nothing here enforces that. In the kernel it holds because SOS is a
// uniprocessor kernel (spec §7) running with interrupts masked. In a user
// process whose threads share a refcount it holds only while nothing preempts
// them. An armed scheduler tick, an interrupt handler touching a refcount, or
// SMP each invalidates every function below, and they must be replaced
// together.
//
// The _4 family is what a 32-bit target needs; a 64-bit target that lowered
// atomics to libcalls would add _8 beside it.

u32 __atomic_load_4(const volatile void *ptr, int memorder) {
    (void)memorder;
    return *(const volatile u32 *)ptr;
}

void __atomic_store_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    *(volatile u32 *)ptr = val;
}

u32 __atomic_fetch_add_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    volatile u32 *p = (volatile u32 *)ptr;
    u32 old = *p;
    *p = old + val;
    return old;
}

u32 __atomic_fetch_sub_4(volatile void *ptr, u32 val, int memorder) {
    (void)memorder;
    volatile u32 *p = (volatile u32 *)ptr;
    u32 old = *p;
    *p = old - val;
    return old;
}

// On failure the current value goes to `*expected`, as the libcall contract
// requires. The sized libcall takes no `weak` argument and both orders are
// ignored, like `memorder` above.
_Bool __atomic_compare_exchange_4(volatile void *ptr, void *expected,
                                  u32 desired, int success_order,
                                  int failure_order) {
    (void)success_order;
    (void)failure_order;
    volatile u32 *p = (volatile u32 *)ptr;
    u32 *e = (u32 *)expected;
    u32 old = *p;
    if (old == *e) {
        *p = desired;
        return 1;
    }
    *e = old;
    return 0;
}

// ---- the arena's extent ---------------------------------------------------
//
// `sosrt`'s bump arena is a linker region, so a package can choose its size
// without conditional compilation, which this toolchain does not have. Each
// linker script reserves `[_arena_start, _arena_end)` inside `.bss` under a
// `PROVIDE`d `ARENA_SIZE`; the two functions below read that extent back.
//
// `extern unsigned char sym[]` is the idiom for a linker symbol: an array's
// name is its address, so nothing here dereferences storage that does not
// exist. The size is the difference of two real addresses rather than an
// absolute `ARENA_SIZE` symbol, matching what `sink.c` does for `.payload`.

extern unsigned char _arena_start[];
extern unsigned char _arena_end[];

usize sos_arena_base(void) { return (usize)_arena_start; }

usize sos_arena_size(void) { return (usize)(_arena_end - _arena_start); }
