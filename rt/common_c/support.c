// SOS common runtime support (designs 140, 172) — the C that must stay C, once.
//
// EVERY LINE IN THIS FILE IS C FOR ONE OF THREE REASONS, and all three are
// PERMANENT. Nothing here is waiting on a language feature; when this file was
// written it also held a bump arena and the four `__saw_rt_*` seams, and design
// 172 part 2 moved those to Saw (`rt/common/src/lib.saw`), which is what leaves
// the reasons below as the whole story.
//
//  1. mem* : a byte-copy loop written in Saw is exactly the pattern LLVM's
//     loop-idiom recognizer rewrites into a call to `memcpy` — which, in a
//     freestanding build where this IS memcpy, is a call to itself. Keeping
//     these in C lets them be compiled with `-fno-builtin`, which is the
//     supported way to say "do not do that". (Same reason libcs write them in
//     assembly or with the same flag.)
//
//  2. the __atomic_* family : a compiler-generated LIBCALL, so it must exist
//     under the name and signature the backend emits, for a build that does not
//     name an atomics extension. There is nothing for Saw to express — the
//     caller is codegen, not source. See the uniprocessor caveat below.
//
//  3. the arena's extent (sawos design 38) : SAW CANNOT NAME A LINKER SYMBOL.
//     That is the same wall `hal/*/kernel/sink.c` meets for `.payload` and
//     `.regions`, and it is answered the same way — two accessors, in the one
//     C file BOTH the kernel and every process image already link, so one
//     answer serves both sides of the M/U split. See the section at the foot
//     of this file.
//
// So this file has one accessor pair and no SEAM of its own: nothing here is a
// per-side hook. The two per-side hooks it used to be written against —
// `sos_rt_write` and `sos_rt_abort`, where a byte goes and how the machine
// stops — are still the system's one runtime seam; they are just Saw on both
// ends now. See `rt/common/src/lib.saw` for that contract, and `spec.md` §5c
// for the C floor as a whole.
//
// What design 172 moved out of the SOS C layer over its two parts: the board
// consoles, the machine stops, the arm64 page tables, the PMP region staging,
// the kernel-fault report, the runtime seams, the arena, and the process side's
// console/abort hooks. What is left, across the whole tree, is this file plus
// four inline-asm leaves.

typedef unsigned int   u32;
typedef unsigned char  u8;
typedef unsigned long  usize;   // ilp32: 32-bit, matches platform `Int`

// ---- mem* (the compiler's implicit block-copy / zero helpers) -------------
//
// Compiled with -fno-builtin; see reason 1 in the header.
//
// AUDIT (sawos design 1, M3 unit 1.5): these loops take no preemption point and
// cannot. Their CALLER is codegen — a struct assignment, an array initializer —
// so there is no source site to write a placement verdict at, and the moves the
// compiler emits are single values rather than bulk regions. The kernel's own
// bulk moves go through `kcore.preempt`, which is where the points are.

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
// REASON 2 AGAIN, and only a 32-bit profile needs them: rv32 has no 64-bit
// divide instruction even with `+m` (which buys the 32-bit pair), so a
// `u64 / u64` in Saw lowers to a call the BACKEND emits and no source names.
// arm64 divides 64 bits natively, so `--gc-sections` drops both there.
//
// SOS started needing them at design 232 unit 1, and the reason is not
// incidental: a Clock reads NANOSECONDS, which is a 64-bit quantity on every
// profile — 32 bits of nanoseconds wraps in four seconds, which is not a clock
// — and the Timer's drift-free coalescing divides a 64-bit lateness by a
// 64-bit interval to get a fire count. The alternative to this file entry was
// advancing the deadline in a LOOP, which is exactly the unbounded kernel loop
// the division was chosen to avoid.
//
// Shift-subtract, most significant bit first: the schoolbook algorithm, and
// deliberately the simple one. It is a fixed 64 iterations with no table and no
// wide intermediate, and — the part that matters — it uses no `/` or `%` on a
// 64-bit value itself, which would be a call back into this function.

typedef unsigned long long u64;

static u64 udivmod64(u64 n, u64 d, u64 *rem) {
    // Division by zero is undefined, and the freestanding convention is
    // all-ones rather than a trap: a kernel that faulted HERE would report a
    // machine fault with no hint of which arithmetic produced it. Saw's own
    // checks are what a caller meets first, so this is the unreachable floor.
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
// CURRENTLY UNREFERENCED, and kept deliberately. Both builds now pass
// `--target-features +m,+a,+c`, so String's refcount traffic lowers to real
// `amoadd.w` instructions and `--gc-sections` drops everything below. They stay
// because a build that does NOT name the A extension — a smaller part, a
// different profile — lowers the same traffic to these libcalls instead, and
// the alternative to keeping them is an unresolved-symbol link failure with no
// hint as to what to write.
//
// THE UNIPROCESSOR CAVEAT, STATED ONCE FOR THE WHOLE FAMILY: plain
// read-modify-write is correct here and nowhere else. SOS v1 is a uniprocessor
// kernel (spec §7) and M1 enables no interrupt sources, so no other agent can
// observe the window. THREE later changes each invalidate every function
// below and must replace them together: enabling interrupts (an ISR touching a
// refcount), SMP, and preemption between threads sharing a refcount. Building
// the Saw object for an ISA with atomics retires them outright.
//
// The _4 family is what a 32-bit target needs. arm64 (M1b) adds _8 beside it —
// the reason these are grouped as a family rather than written as four
// one-offs.

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

// ---- the arena's extent (sawos design 38) ---------------------------------
//
// REASON 3: `sosrt`'s bump arena is a LINKER REGION now rather than a `static`
// in the shared module, so that a package can choose its size without a
// conditional compilation this toolchain does not have. The linker scripts
// reserve `[_arena_start, _arena_end)` inside `.bss` under a `PROVIDE`d
// `ARENA_SIZE`; these two functions are how the Saw side reads that back,
// because Saw cannot name a linker symbol.
//
// `extern unsigned char sym[]` is the idiom for a linker symbol: an array's
// name IS its address, so nothing here dereferences storage that does not
// exist. The SIZE comes from subtracting the two addresses rather than from an
// absolute `ARENA_SIZE` symbol — same number, and a pair of real addresses is
// what `sink.c` already does for `.payload`, so there is one idiom in the tree
// and not two.
//
// BOTH SIDES LINK THIS FILE (the kernel through `_build_shared`, every process
// image through its manifest's `[sos.<triple>] native`), which is what makes
// one definition enough for a module that serves an M-mode kernel and a U-mode
// process alike.

extern unsigned char _arena_start[];
extern unsigned char _arena_end[];

usize sos_arena_base(void) { return (usize)_arena_start; }

usize sos_arena_size(void) { return (usize)(_arena_end - _arena_start); }
