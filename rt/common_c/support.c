// SOS common runtime support: the C that must stay C, once.
//
// Every line in this file is C for one of three reasons, all permanent:
//
//  1. mem*: a byte-copy loop written in Saw is exactly the pattern LLVM's
//     loop-idiom recognizer rewrites into a call to `memcpy`, which, in a
//     freestanding build where this is memcpy, is a call to itself. Keeping
//     these in C lets them be compiled with `-fno-builtin`, the supported way
//     to say "do not do that".
//
//  2. the __atomic_* family and the 64-bit division helpers: compiler-generated
//     libcalls, so they must exist under the name and signature the backend
//     emits. There is nothing for Saw to express, since the caller is codegen,
//     not source. See the uniprocessor caveat below.
//
//  3. the arena's extent: Saw cannot name a linker symbol. This is the same
//     wall `hal/*/kernel/sink.c` meets for `.payload` and `.regions`, answered
//     the same way, in the one C file both the kernel and every process image
//     link. See the section at the foot of this file.
//
// The runtime's two per-side hooks, `sos_rt_write` and `sos_rt_abort` (where a
// byte goes and how the machine stops), are Saw on both ends. See
// `rt/common/src/lib.saw` for that contract, and `spec.md` §5c for the C floor
// as a whole.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // matches platform `Int` (ilp32 and LP64)

// ---- mem* (the compiler's implicit block-copy / zero helpers) -------------
//
// Compiled with -fno-builtin; see reason 1 in the header.
//
// These loops take no preemption point and cannot. Their caller is codegen (a
// struct assignment, an array initializer), so there is no source site to place
// one at, and the moves the compiler emits are single values rather than bulk
// regions. The kernel's own bulk moves go through `kcore.preempt`, which is
// where the points are.

void *memset(void *dst, int c, usize n) {
    u8 *d = (u8 *)dst;
    for (usize i = 0; i < n; i++) d[i] = (u8)c;
    return dst;
}

void *memcpy(void *dst, const void *src, usize n) {
    u8 *d = (u8 *)dst; const u8 *s = (const u8 *)src;
    for (usize i = 0; i < n; i++) d[i] = s[i];
    return dst;
}

void *memmove(void *dst, const void *src, usize n) {
    u8 *d = (u8 *)dst; const u8 *s = (const u8 *)src;
    if (d < s) {
        for (usize i = 0; i < n; i++) d[i] = s[i];
    } else {
        for (usize i = n; i > 0; i--) d[i - 1] = s[i - 1];
    }
    return dst;
}

// ---- 64-bit division libcalls --------------------------------------------
//
// Reason 2 again, and only a 32-bit profile needs them: rv32 has no 64-bit
// divide instruction even with `+m` (which buys the 32-bit pair), so a
// `u64 / u64` in Saw lowers to a call the backend emits and no source names.
// arm64 divides 64 bits natively, so `--gc-sections` drops both there.
//
// A Clock reads nanoseconds, a 64-bit quantity on every profile (32 bits of
// nanoseconds wraps in four seconds), and the Timer's drift-free coalescing
// divides a 64-bit lateness by a 64-bit interval to get a fire count. Don't
// advance the deadline in a loop instead: that is an unbounded kernel loop.
//
// Shift-subtract, most significant bit first: a fixed 64 iterations with no
// table and no wide intermediate. It uses no `/` or `%` on a 64-bit value
// itself, which would be a call back into this function.

typedef unsigned long long u64;

static u64 udivmod64(u64 n, u64 d, u64 *rem) {
    // Division by zero is undefined, and the freestanding convention is
    // all-ones rather than a trap: a fault here would report a machine fault
    // with no hint of which arithmetic produced it. Saw's own checks are what a
    // caller meets first, so this is the unreachable floor.
    if (d == 0) {
        if (rem) *rem = 0;
        return ~(u64)0;
    }
    u64 q = 0, r = 0;
    for (int i = 63; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1ULL);
        if (r >= d) {
            r -= d;
            q |= (1ULL << i);
        }
    }
    if (rem) *rem = r;
    return q;
}

u64 __udivdi3(u64 n, u64 d) { return udivmod64(n, d, 0); }

u64 __umoddi3(u64 n, u64 d) { u64 r; udivmod64(n, d, &r); return r; }

// ---- atomic libcalls, by width -------------------------------------------
//
// The riscv32 virt and flat builds pass `--target-features +m,+a,+c`, so
// String's refcount traffic lowers to real `amoadd.w` instructions there and
// `--gc-sections` drops everything below. The ESP32-C3 build is RV32IMC, with
// no A extension, so the same traffic lowers to these libcalls; without them
// that build is an unresolved-symbol link failure with no hint what to write.
//
// The uniprocessor caveat, stated once for the whole family: plain
// read-modify-write is correct only while no involuntary switch can land
// between the read and the write. In the kernel that is guaranteed: SOS is a
// uniprocessor kernel (spec §7) and the kernel runs with interrupts masked. In
// a user process whose threads share a refcount it holds only while nothing
// preempts: the kernel switches threads involuntarily only on a scheduler tick,
// which `kernel/main.saw` does not arm and the preemption harness kernels do.
// An armed tick, an interrupt handler touching a refcount, or SMP each
// invalidates every function below, and they must be replaced together.
// Building the Saw object for an ISA with atomics retires them outright.
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

// ---- the arena's extent ---------------------------------------------------
//
// Reason 3: `sosrt`'s bump arena is a linker region, so a package can choose
// its size without conditional compilation, which this toolchain does not have.
// The linker scripts reserve `[_arena_start, _arena_end)` inside `.bss` under a
// `PROVIDE`d `ARENA_SIZE`; these two functions are how the Saw side reads that
// back.
//
// `extern unsigned char sym[]` is the idiom for a linker symbol: an array's
// name is its address, so nothing here dereferences storage that does not
// exist. The size comes from subtracting the two addresses rather than from an
// absolute `ARENA_SIZE` symbol; a pair of real addresses is what `sink.c`
// already does for `.payload`, so there is one idiom in the tree.
//
// Both sides link this file (the kernel through `_build_shared`, every process
// image through its manifest's `[sos.<triple>] native`), so one definition
// serves an M-mode kernel and a U-mode process alike.

extern unsigned char _arena_start[];
extern unsigned char _arena_end[];

usize sos_arena_base(void) { return (usize)_arena_start; }

usize sos_arena_size(void) { return (usize)(_arena_end - _arena_start); }
