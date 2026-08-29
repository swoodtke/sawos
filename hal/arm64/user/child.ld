/* SOS CHILD process linker script (sawos design 2) — arm64, Profile B.
 *
 * ROOT'S SCRIPT WITH ONE NUMBER CHANGED, and the number is the whole design.
 * The kernel's map is a static identity map, so every process shares one
 * address space here too — each child is linked at a DISTINCT base assigned in
 * the build's config, and `process_create` validates every segment against the
 * destination region root hands it.
 *
 *   0x4024_0000  CHILD_LOAD_BASE  — one region above root's
 *   0x4027_C000  stack bottom     — the kernel grants this RW separately
 *   0x4028_0000  CHILD_REGION_TOP — initial sp (256 KiB region, 16 KiB stack)
 *
 * THE WINDOW IS THE CONSTRAINT ON THIS PROFILE, and it is why the base is not
 * free: EL0 can only be granted pages inside the HAL's GRANT WINDOW, the first
 * 4 MiB of RAM (0x4000_0000..0x4040_0000), because that is the span the level-3
 * tables cover. Root's region ends at 0x4024_0000 and the window ends at
 * 0x4040_0000, so a child's destination has to sit between them. There is room
 * for seven more 256 KiB regions; a build that wanted more would have to widen
 * `GRANT_PAGES` in the kernel HAL, which is the honest place for that decision.
 *
 * WHAT DIFFERS FROM PROFILE A, and it is the whole reason this file is not a
 * copy of its twin: protection granularity here is the PAGE. Two segments
 * sharing a page would be granted the union of their permissions, so `.text`
 * and `.data` are each page-aligned — which is what keeps a child's code from
 * becoming writable because its data happened to start 200 bytes later. The
 * kernel's loader checks the alignment either way rather than assuming a linker
 * script got it right.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

MEMORY {
    CHILD (rwxa) : ORIGIN = 0x40240000, LENGTH = 240K
}

SECTIONS {
    . = 0x40240000;

    /* Code + read-only data: one R+X segment, page-aligned at both ends. */
    .text : ALIGN(4096) {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4096);
    } > CHILD

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > CHILD

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(4096);
    } > CHILD

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
