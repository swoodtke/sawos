/* For a `fp_planted` kernel on the ESP32-S3: an FP-option arithmetic op,
 * `add.s`, in a sized function, which the build's disassembly check must
 * refuse.
 *
 * u32 ktest_fp_planted(u32 word): `word`, or what the planted
 * instruction leaves in it. */

    .section .text.ktest_fp_planted, "ax"
    .align  4
    .global ktest_fp_planted
    .type   ktest_fp_planted, @function
ktest_fp_planted:
    add.s   f0, f1, f2
    ret
    .size   ktest_fp_planted, . - ktest_fp_planted
