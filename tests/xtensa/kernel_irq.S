/* Two ways for the kernel to take an interrupt outside its idle wait, for
 * the `kernel_irq_*` kernels on the ESP32-S3. Each is a call0 leaf the
 * kernel calls with an enabled interrupt pending; each returns only if the
 * interrupt was not taken or the kernel vector returned from it.
 *
 * void ktest_irq_window(void): INTLEVEL 0 for a few instructions, then 15
 * again, as a kernel path that unmasked by mistake would.
 *
 * void ktest_stray_waiti(void): a `waiti 0` of its own, which is not the one
 * in `boot.S`'s `sos_idle_wait`. */

    .section .text.ktest_irq_window, "ax"
    .align  4
    .global ktest_irq_window
    .type   ktest_irq_window, @function
ktest_irq_window:
    rsil    a2, 0
    rsync
    nop
    nop
    nop
    nop
    rsil    a2, 15
    rsync
    ret

    .section .text.ktest_stray_waiti, "ax"
    .align  4
    .global ktest_stray_waiti
    .type   ktest_stray_waiti, @function
ktest_stray_waiti:
    waiti   0
    ret
