// The latency cases' line: the console UART's transmit interrupt on the arm64
// `virt` board's pl011. See `riscv32.c`.
//
// How it fires once per enable: the pl011 model latches its TX interrupt
// level whenever the transmitter finishes a byte, and only a write to ICR
// clears it; enabling TXIM (IMSC bit 5) lets a latched level through to the
// line, and clearing TXIM lowers it. The kernel's console has transmitted
// before any process runs, so the level is latched when the process starts.
// Neither function writes ICR: clearing the latch before enabling would
// leave nothing to assert, which the model's trace shows as `irq state 0`
// after the IMSC write, and `irq state 1` after each of four enables with no
// ICR write (QEMU's `-trace 'pl011_*'`). The latch is rewritten by every
// console byte, so a round enables, waits and disables without printing in
// between.
//
// `unsigned long` is this profile's `UInt`.

#define UART_BASE 0x09000000ul
#define IMSC (*(volatile unsigned int *)(UART_BASE + 0x38))
#define INT_TX 0x20

// The console UART's line: the GIC interrupt number of its pl011.
unsigned long latency_line(void) { return 33; }

void latency_source_on(void) { IMSC = INT_TX; }
void latency_source_off(void) { IMSC = 0; }
