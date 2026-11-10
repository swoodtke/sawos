// Board facts the interrupt tests need and userspace cannot see, for the arm64
// `virt` board. See `riscv32.c`. `unsigned long` is this profile's `UInt`.

// The console UART's line: the GIC interrupt number of its pl011.
unsigned long driver_irq_line(void) {
    return 33;
}

// The kernel's own timer line: the EL1 physical timer's private interrupt. It
// is an ordinary controller line on this board, so only the kernel's policy
// refuses it.
unsigned long timer_irq_line(void) {
    return 30;
}
