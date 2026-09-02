/* SOS SECOND-CHILD process linker script (sawos design 21, M4 unit 5) —
 * arm64, Profile B.
 *
 * **`child.ld` WITH ONE NUMBER CHANGED, AND ITS OWN FILE'S PREDICTION KEPT.**
 * That file counts the room this profile has — *"There is room for seven more
 * 256 KiB regions"* — and unit 5 spends the first of them: driver-as-service
 * needs a driver child and a client child alive at once, which is the first
 * case in this tree with two children.
 *
 *   0x4028_0000  CHILD2_LOAD_BASE — one region above the first child's
 *   0x402B_C000  stack bottom     — the kernel grants this RW separately
 *   0x402C_0000  CHILD2_REGION_TOP — initial sp (256 KiB region, 16 KiB stack)
 *
 * **THE GRANT WINDOW IS STILL THE CONSTRAINT, and this region is inside it.**
 * EL0 can only be granted pages in the first 4 MiB of RAM
 * (0x4000_0000..0x4040_0000), because that is the span the level-3 tables
 * cover; this region ends at 0x402C_0000, well below the top. Six 256 KiB
 * regions remain after it.
 *
 * **THE BASE IS `tools/sos_runner.py`'s ARITHMETIC, NOT A SECOND OPINION**:
 * `child_region_base + 1 * CHILD_REGION_LEN`. A package's manifest picks a
 * SCRIPT and the runner's `_region_rows` assigns destinations BY INDEX, so a
 * package linked with this file is a package the case must list SECOND.
 *
 * **IT COLLIDES WITH THE RAM POOL, DELIBERATELY AND HARMLESSLY** — the pool
 * starts at this same 0x4028_0000, for the same "one region above the one child
 * region" reasoning. A case cannot have two children AND a pool, and
 * `_region_rows` refuses that combination rather than emitting overlapping
 * rows.
 *
 * Page alignment of `.text` and `.data` is `child.ld`'s rule and is kept for
 * `child.ld`'s reason: protection granularity here is the PAGE, so two segments
 * sharing one would be granted the union of their permissions.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

MEMORY {
    CHILD2 (rwxa) : ORIGIN = 0x40280000, LENGTH = 240K
}

SECTIONS {
    . = 0x40280000;

    /* Code + read-only data: one R+X segment, page-aligned at both ends. */
    .text : ALIGN(4096) {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4096);
    } > CHILD2

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > CHILD2

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(4096);
    } > CHILD2

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
