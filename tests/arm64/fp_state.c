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

__asm__(
    ".text\n"
    ".balign 4\n"

    ".macro FILL n\n"
    "    add     x1, x1, x2\n"
    "    dup     v\\n\\().2d, x1\n"
    "    add     x3, x1, #1\n"
    "    mov     v\\n\\().d[1], x3\n"
    ".endm\n"

    ".macro CHECK n\n"
    "    add     x1, x1, x2\n"
    "    umov    x4, v\\n\\().d[0]\n"
    "    cmp     x4, x1\n"
    "    cinc    x6, x6, ne\n"
    "    add     x3, x1, #1\n"
    "    umov    x4, v\\n\\().d[1]\n"
    "    cmp     x4, x3\n"
    "    cinc    x6, x6, ne\n"
    ".endm\n"

    ".macro ZERO n\n"
    "    umov    x4, v\\n\\().d[0]\n"
    "    cmp     x4, #0\n"
    "    cinc    x6, x6, ne\n"
    "    umov    x4, v\\n\\().d[1]\n"
    "    cmp     x4, #0\n"
    "    cinc    x6, x6, ne\n"
    ".endm\n"

    ".globl fp_fill\n"
    "fp_fill:\n"
    "    mov     x1, x0\n"
    "    mov     x2, #0x1001\n"
    ".irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31\n"
    "    FILL    \\n\n"
    ".endr\n"
    /* FPSR: the cumulative flags IOC..IXC (bits 0-4). FPCR: flush-to-zero
     * (bit 24) on an odd seed. */
    "    and     x5, x0, #0x1f\n"
    "    msr     fpsr, x5\n"
    "    and     x5, x0, #1\n"
    "    lsl     x5, x5, #24\n"
    "    msr     fpcr, x5\n"
    "    ret\n"

    ".globl fp_check\n"
    "fp_check:\n"
    "    mov     x7, x0\n"
    "    mov     x1, x0\n"
    "    mov     x2, #0x1001\n"
    "    mov     x6, xzr\n"
    ".irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31\n"
    "    CHECK   \\n\n"
    ".endr\n"
    "    mrs     x5, fpsr\n"
    "    and     x8, x7, #0x1f\n"
    "    cmp     x5, x8\n"
    "    cinc    x6, x6, ne\n"
    "    mrs     x5, fpcr\n"
    "    and     x8, x7, #1\n"
    "    lsl     x8, x8, #24\n"
    "    cmp     x5, x8\n"
    "    cinc    x6, x6, ne\n"
    "    mov     x0, x6\n"
    "    ret\n"

    ".globl fp_zero_count\n"
    "fp_zero_count:\n"
    "    mov     x6, xzr\n"
    ".irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31\n"
    "    ZERO    \\n\n"
    ".endr\n"
    "    mrs     x5, fpsr\n"
    "    cmp     x5, #0\n"
    "    cinc    x6, x6, ne\n"
    "    mrs     x5, fpcr\n"
    "    cmp     x5, #0\n"
    "    cinc    x6, x6, ne\n"
    "    mov     x0, x6\n"
    "    ret\n"
);
