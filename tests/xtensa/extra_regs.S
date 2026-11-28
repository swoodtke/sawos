/* User-side helpers for the ESP32-S3 cases on the registers no coprocessor
 * gates: BR, MAC16's ACCLO, ACCHI and M0..M3, and MISC0..MISC3, eleven in
 * all, which the trap frame carries. Call0 functions in assembly, so every
 * register each touches is named here; compiled code touches none of the
 * eleven.
 *
 * `xregs_fill(seed)` loads all eleven with a pattern derived from `seed`;
 * `xregs_check(seed)` answers how many no longer hold it, 0 when they
 * survived; `xregs_nonzero()` answers how many are not zero.
 *
 * The pattern: BR (16 bits) is the low half of `seed ^ (seed >> 16)`; ACCLO
 * is `seed + 1`; ACCHI, which holds the accumulator's bits 39:32 and reads
 * back sign-extended from them, is bits 21:16 of `seed`, so it reads back as
 * written; M0..M3 are `seed + 2` to `seed + 5` and MISC0..MISC3 `seed + 6`
 * to `seed + 9`. */

    .text

/* a8 = the register's expected value for the seed in a2 at offset \k. */
    .macro AT k
    addi    a8, a2, \k
    .endm

/* Add 1 to a7 if a5 differs from a8. */
    .macro DIFFER
    sub     a5, a5, a8
    movi    a6, 1
    movnez  a5, a6, a5
    add     a7, a7, a5
    .endm

/* Add 1 to a7 if a5 is not zero. */
    .macro NONZERO
    movi    a6, 1
    movnez  a5, a6, a5
    add     a7, a7, a5
    .endm

/* BR and ACCHI as `xregs_fill` writes them for the seed in a2, into a9 and
 * a10. */
    .macro EXPECT
    srli    a9, a2, 16
    xor     a9, a9, a2
    extui   a9, a9, 0, 16
    extui   a10, a2, 16, 6
    .endm

    .align  4
    .global xregs_fill
    .type   xregs_fill, @function
xregs_fill:
    EXPECT
    wsr     a9, br
    wsr     a10, acchi
    AT      1
    wsr     a8, acclo
    AT      2
    wsr     a8, m0
    AT      3
    wsr     a8, m1
    AT      4
    wsr     a8, m2
    AT      5
    wsr     a8, m3
    AT      6
    wsr     a8, misc0
    AT      7
    wsr     a8, misc1
    AT      8
    wsr     a8, misc2
    AT      9
    wsr     a8, misc3
    ret
    .size   xregs_fill, . - xregs_fill

    .align  4
    .global xregs_check
    .type   xregs_check, @function
xregs_check:
    movi    a7, 0
    EXPECT
    rsr     a5, br
    mov     a8, a9
    DIFFER
    rsr     a5, acchi
    mov     a8, a10
    DIFFER
    rsr     a5, acclo
    AT      1
    DIFFER
    rsr     a5, m0
    AT      2
    DIFFER
    rsr     a5, m1
    AT      3
    DIFFER
    rsr     a5, m2
    AT      4
    DIFFER
    rsr     a5, m3
    AT      5
    DIFFER
    rsr     a5, misc0
    AT      6
    DIFFER
    rsr     a5, misc1
    AT      7
    DIFFER
    rsr     a5, misc2
    AT      8
    DIFFER
    rsr     a5, misc3
    AT      9
    DIFFER
    mov     a2, a7
    ret
    .size   xregs_check, . - xregs_check

    .align  4
    .global xregs_nonzero
    .type   xregs_nonzero, @function
xregs_nonzero:
    movi    a7, 0
    rsr     a5, br
    NONZERO
    rsr     a5, acclo
    NONZERO
    rsr     a5, acchi
    NONZERO
    rsr     a5, m0
    NONZERO
    rsr     a5, m1
    NONZERO
    rsr     a5, m2
    NONZERO
    rsr     a5, m3
    NONZERO
    rsr     a5, misc0
    NONZERO
    rsr     a5, misc1
    NONZERO
    rsr     a5, misc2
    NONZERO
    rsr     a5, misc3
    NONZERO
    mov     a2, a7
    ret
    .size   xregs_nonzero, . - xregs_nonzero
