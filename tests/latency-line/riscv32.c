// The latency cases' line: the console UART's transmitter-empty interrupt on
// the riscv32 `virt` board's ns16550. Enabling it with the transmitter empty
// asserts the line at once, and clearing the enable lowers it, so each
// `latency_source_on` is one fire for a process that turns the source off
// again before it acks.
//
// The process maps the console's page (`device`) and this file reaches the
// registers through the same address. `unsigned int` is this profile's `UInt`.

#define UART_BASE 0x10000000u
#define IER (*(volatile unsigned char *)(UART_BASE + 1))
#define IER_TX_EMPTY 0x02

// The console UART's line: the PLIC source of its ns16550.
unsigned int latency_line(void) { return 10; }

void latency_source_on(void) { IER = IER_TX_EMPTY; }
void latency_source_off(void) { IER = 0; }
