/* For the `fp_planted` kernel on the ESP32-S3: one FP instruction pair in
 * kernel code, which the build's disassembly check must refuse.
 *
 * u32 ktest_fp_planted(u32 word): `word`, moved through f0. */

    .section .text.ktest_fp_planted, "ax"
    .align  4
    .global ktest_fp_planted
    .type   ktest_fp_planted, @function
ktest_fp_planted:
    wfr     f0, a2
    rfr     a2, f0
    ret
    .size   ktest_fp_planted, . - ktest_fp_planted
