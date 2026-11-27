/* User-side FP/SIMD helpers for the arm64 floating-point state cases. Each
 * routine is assembly so the compiler never has a vector register of its own
 * live across a call, and every register it touches is named here.
 *
 * `fp_fill(seed)` loads V0-V31 with a pattern derived from `seed`, and `FPSR`
 * and `FPCR` with seed-dependent bits. `fp_check(seed)` counts the registers
 * that no longer hold that pattern, 0 when state survived. `fp_zero_count()`
 * counts the nonzero registers among V0-V31, `FPSR` and `FPCR`, 0 for a
 * thread that was handed zeroed state. Each begins with an FP/SIMD
 * instruction, so each is the calling thread's first use when it is the first.
 *
 * Register n holds two doublewords: the low one is
 * `seed + (n + 1) * STRIDE`, the high one that plus one. The pattern differs
 * per register and per seed, and the two halves differ from each other. */

.text
.balign 4

.macro FILL n
    add     x1, x1, x2
    dup     v\n\().2d, x1
    add     x3, x1, #1
    mov     v\n\().d[1], x3
.endm

.macro CHECK n
    add     x1, x1, x2
    umov    x4, v\n\().d[0]
    cmp     x4, x1
    cinc    x6, x6, ne
    add     x3, x1, #1
    umov    x4, v\n\().d[1]
    cmp     x4, x3
    cinc    x6, x6, ne
.endm

.macro ZERO n
    umov    x4, v\n\().d[0]
    cmp     x4, #0
    cinc    x6, x6, ne
    umov    x4, v\n\().d[1]
    cmp     x4, #0
    cinc    x6, x6, ne
.endm

.globl fp_fill
fp_fill:
    mov     x1, x0
    mov     x2, #0x1001
.irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    FILL    \n
.endr
/* FPSR: the cumulative flags IOC..IXC (bits 0-4). FPCR: flush-to-zero
 * (bit 24) on an odd seed. */
    and     x5, x0, #0x1f
    msr     fpsr, x5
    and     x5, x0, #1
    lsl     x5, x5, #24
    msr     fpcr, x5
    ret

.globl fp_check
fp_check:
    mov     x7, x0
    mov     x1, x0
    mov     x2, #0x1001
    mov     x6, xzr
.irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    CHECK   \n
.endr
    mrs     x5, fpsr
    and     x8, x7, #0x1f
    cmp     x5, x8
    cinc    x6, x6, ne
    mrs     x5, fpcr
    and     x8, x7, #1
    lsl     x8, x8, #24
    cmp     x5, x8
    cinc    x6, x6, ne
    mov     x0, x6
    ret

.globl fp_zero_count
fp_zero_count:
    mov     x6, xzr
.irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    ZERO    \n
.endr
    mrs     x5, fpsr
    cmp     x5, #0
    cinc    x6, x6, ne
    mrs     x5, fpcr
    cmp     x5, #0
    cinc    x6, x6, ne
    mov     x0, x6
    ret
