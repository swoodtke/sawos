/* For a `fp_planted` kernel on the ESP32-S3: an FP instruction pair in code
 * with no symbol size or type, as hand-written assembly often is, which the
 * build's disassembly check must refuse.
 *
 * u32 ktest_fp_planted(u32 word): `word`, or what the planted
 * instruction leaves in it. */

    .section .text.ktest_fp_planted, "ax"
    .align  4
    .global ktest_fp_planted
ktest_fp_planted:
    wfr     f0, a2
    rfr     a2, f0
    ret
