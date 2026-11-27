// The virtio network driver's barrier on arm64: a full-system data
// synchronization barrier, so every load and store before it, the queue
// memory's and the transport's registers' alike, completes before any after
// it, for the device as well as for the processor. It is the barrier virtio's
// split rings need around the available index and the used ring (virtio 1.2
// section 2.7.13), and the one for hardware devices that `ORDER_PLATFORM` asks
// for. Saw has no spelling of a barrier instruction, so it is C.

void sos_netdrv_fence(void) {
    __asm__ volatile("dsb sy" ::: "memory");
}
