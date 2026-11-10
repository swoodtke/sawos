// Board facts the interrupt tests need and userspace cannot see, for the
// riscv32 `virt` board. Each test package links the file for its target beside
// its syscall stub (`native` in its Saw.toml) and declares what it calls in an
// `extern "C"` block. `unsigned int` is this profile's `UInt`.

// The console UART's line: the PLIC source of its ns16550.
unsigned int driver_irq_line(void) {
    return 10;
}

// The kernel's own timer line. It lies above every source the PLIC wires, so
// binding it is refused as a line the board lacks as well as the tick's.
unsigned int timer_irq_line(void) {
    return 1024;
}
