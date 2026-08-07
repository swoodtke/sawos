/* SOS root server linker script (design 140) — riscv32, Profile A.
 *
 * Root is loaded by the kernel at a FIXED address: there is no relocation and
 * no MMU on this profile, so the addresses here and the root-region constants
 * in sos/kernel/core/lib.saw are one decision recorded twice. The kernel
 * validates every segment against the region and rejects an image that does
 * not fit, so a mismatch is a clean diagnostic rather than a corrupted kernel.
 *
 *   0x8020_0000  ROOT_LOAD_BASE   — segments load from here
 *   0x8023_C000  stack bottom     — the kernel grants this RW separately
 *   0x8024_0000  ROOT_REGION_TOP  — initial sp
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live.
 *
 * Sections are grouped so the image needs FEW loadable segments: each one costs
 * a PMP region, and the kernel's budget is small (see virt.ld). Read-only data
 * shares the executable segment rather than claiming a region of its own — W^X
 * is preserved (nothing writable is executable), which is the property that
 * matters.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
    ROOT (rwxa) : ORIGIN = 0x80200000, LENGTH = 240K
}

SECTIONS {
    . = 0x80200000;

    /* Code + read-only data: one R+X segment. */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > ROOT

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > ROOT

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
    } > ROOT

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
