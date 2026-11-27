// The virtio network driver's barrier on riscv32: a full fence over memory
// and I/O, so every load and store before it, the queue memory's and the
// transport's registers' alike, is ordered before every one after it, for the
// device as well as for the processor. It is the barrier virtio's split rings
// need around the available index and the used ring (virtio 1.2 section
// 2.7.13), and the one for hardware devices that `ORDER_PLATFORM` asks for.
// Saw has no spelling of a fence instruction, so it is C.

void sos_netdrv_fence(void) {
    __asm__ volatile("fence iorw, iorw" ::: "memory");
}
