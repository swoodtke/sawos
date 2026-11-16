/* For the `idle_wake` kernel on the ESP32-S3.
 *
 * u32 ktest_ps(void): PS, as the kernel runs. */

    .section .text.ktest_ps, "ax"
    .align  4
    .global ktest_ps
    .type   ktest_ps, @function
ktest_ps:
    rsr     a2, ps
    ret
