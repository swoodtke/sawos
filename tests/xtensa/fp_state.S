/* User-side FP helpers for the ESP32-S3 floating-point state cases: call0
 * functions, each assembly so that no compiler has an FP register of its own
 * live across a call, and every register each touches is named here.
 *
 * A thread's FP state is f0..f15, FCR and FSR, eighteen registers.
 * `fp_fill(seed)` loads all eighteen with a pattern derived from `seed`;
 * `fp_check(seed)` answers how many no longer hold it, 0 when the state
 * survived; `fp_zero_count()` answers how many are not zero, 0 for a thread
 * that was handed zeroed state. Any of them may be the calling thread's first
 * use: its first FP instruction traps, and runs again once the kernel has
 * loaded the thread's state. `fp_cpenable_clear()` turns the coprocessor off
 * from user mode, which no privilege stops on this core.
 *
 * The pattern: f(n) holds `seed + (n + 1) * STRIDE`, so it differs per
 * register and per seed; FCR's rounding mode (bits 1:0) is the seed's low two
 * bits and FSR's five flags (bits 11:7) its low five. Only those fields of
 * FCR and FSR are written, so the values read back are the ones written. */

    .equ STRIDE, 0x1001

    .text

    .macro FILL n
    add     a3, a3, a4
    wfr     f\n, a3
    .endm

    .macro CHECK n
    add     a3, a3, a4
    rfr     a5, f\n
    sub     a5, a5, a3
    movi    a6, 1
    movnez  a5, a6, a5
    add     a7, a7, a5
    .endm

    .macro NONZERO n
    rfr     a5, f\n
    movi    a6, 1
    movnez  a5, a6, a5
    add     a7, a7, a5
    .endm

/* FCR and FSR as `fp_fill` writes them for the seed in a2, into a8 and a9. */
    .macro EXPECT
    extui   a8, a2, 0, 2
    extui   a9, a2, 0, 5
    slli    a9, a9, 7
    .endm

/* Add 1 to a7 if a5 differs from \want. */
    .macro DIFFER want
    sub     a5, a5, \want
    movi    a6, 1
    movnez  a5, a6, a5
    add     a7, a7, a5
    .endm

    .align  4
    .global fp_fill
    .type   fp_fill, @function
fp_fill:
    mov     a3, a2
    movi    a4, STRIDE
    .irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    FILL    \n
    .endr
    EXPECT
    wur     a8, fcr
    wur     a9, fsr
    ret
    .size   fp_fill, . - fp_fill

    .align  4
    .global fp_check
    .type   fp_check, @function
fp_check:
    mov     a3, a2
    movi    a4, STRIDE
    movi    a7, 0
    .irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    CHECK   \n
    .endr
    EXPECT
    rur     a5, fcr
    DIFFER  a8
    rur     a5, fsr
    DIFFER  a9
    mov     a2, a7
    ret
    .size   fp_check, . - fp_check

    .align  4
    .global fp_zero_count
    .type   fp_zero_count, @function
fp_zero_count:
    movi    a7, 0
    .irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    NONZERO \n
    .endr
    rur     a5, fcr
    movi    a6, 1
    movnez  a5, a6, a5
    add     a7, a7, a5
    rur     a5, fsr
    movnez  a5, a6, a5
    add     a7, a7, a5
    mov     a2, a7
    ret
    .size   fp_zero_count, . - fp_zero_count

    .align  4
    .global fp_cpenable_clear
    .type   fp_cpenable_clear, @function
fp_cpenable_clear:
    movi    a2, 0
    wsr     a2, cpenable
    rsync
    ret
    .size   fp_cpenable_clear, . - fp_cpenable_clear
