// The latency cases' line: UART0's transmit-FIFO-empty interrupt on the
// ESP32-S3. See `riscv32.c`.
//
// `unsigned long` is this profile's `UInt`.

#define UART_BASE 0x60000000ul
#define INT_ENA (*(volatile unsigned int *)(UART_BASE + 0x0C))
#define INT_CLR (*(volatile unsigned int *)(UART_BASE + 0x10))
#define INT_TXFIFO_EMPTY (1u << 1)

// UART0's source in the interrupt matrix.
unsigned long latency_line(void) { return 27; }

void latency_source_on(void) { INT_ENA = INT_TXFIFO_EMPTY; }
void latency_source_off(void) { INT_ENA = 0; INT_CLR = INT_TXFIFO_EMPTY; }
