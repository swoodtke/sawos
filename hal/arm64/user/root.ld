/* SOS root server linker script (design 162) — arm64, Profile B.
 *
 * Root is loaded by the kernel at a FIXED address: the kernel's map is a static
 * identity map, so the addresses here and the root-region constants in
 * `sos/hal/arm64/kernel/lib.saw` are one decision recorded twice. The kernel
 * validates every segment against the region and rejects an image that does not
 * fit, so a mismatch is a clean diagnostic rather than a corrupted kernel.
 *
 *   0x4020_0000  ROOT_LOAD_BASE   — segments load from here
 *   0x4023_C000  stack bottom     — the kernel grants this RW separately
 *   0x4024_0000  ROOT_REGION_TOP  — initial sp
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live.
 *
 * WHAT DIFFERS FROM PROFILE A, and it is the whole reason this file is not a
 * copy: protection granularity here is the PAGE. Two segments sharing a page
 * would be granted the union of their permissions, so `.text` and `.data` are
 * each page-aligned — which is what keeps root's code from becoming writable
 * because its data happened to start 200 bytes later. Profile A grants at
 * four-byte granularity and needs none of this; the kernel's loader checks the
 * alignment either way rather than assuming a linker script got it right.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

MEMORY {
    ROOT (rwxa) : ORIGIN = 0x40200000, LENGTH = 240K
}

SECTIONS {
    . = 0x40200000;

    /* Code + read-only data: one R+X segment, page-aligned at both ends. */
    .text : ALIGN(4096) {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4096);
    } > ROOT

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > ROOT

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(4096);
    } > ROOT

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
