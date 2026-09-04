/* SOS THIRD-CHILD process linker script (sawos design 36, M5 unit 6b) —
 * riscv32, Profile A.
 *
 * **`child2.ld` WITH ONE NUMBER CHANGED, AND IT EXISTS BECAUSE A THIRD
 * CONCURRENT CHILD IS THE THING UNIT 6b PROVES.** `MAX_PROCESSES` is 3, so root
 * plus two children IS the compiled floor; a third child alive at the same time
 * is a process in a DONATED slot, and on this profile it needs its own load
 * address like every other image, because there is no translation here — a
 * child image runs at exactly the base it was linked at
 * (`hal.image_link_base` is the identity).
 *
 *   0x802C_0000  CHILD3_LOAD_BASE — one region above the second child's
 *   0x802F_C000  stack bottom     — the kernel grants this RW separately
 *   0x8030_0000  CHILD3_REGION_TOP — initial sp (256 KiB region, 16 KiB stack)
 *
 * **THE BASE IS `tools/sos_runner.py`'s ARITHMETIC, NOT A SECOND OPINION**:
 * `child_region_base + 2 * CHILD_REGION_LEN`. A package linked with this file is
 * a package the case must list THIRD — the same index coupling `child.ld` and
 * `child2.ld` both write down at both ends.
 *
 * **THE aarch64 SIDE NEEDS NO SUCH FILE, and the asymmetry is design 33's.**
 * Since placement landed, every arm64 user image links at one canonical base
 * (`hal/arm64/user/user.ld`) and the kernel translates it to whichever frames
 * the destination region occupies — so ONE script serves any number of
 * concurrent children there. This file is the price of identity placement, and
 * it is why a fourth concurrent child on this profile would want a fourth.
 *
 * Everything else — the section grouping, the 240 KiB length that stops at the
 * stack bottom, the discarded notes — is `child.ld`'s, for `child.ld`'s
 * reasons.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
    CHILD3 (rwxa) : ORIGIN = 0x802C0000, LENGTH = 240K
}

SECTIONS {
    . = 0x802C0000;

    /* Code + read-only data: one R+X segment. */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD3

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD3

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
    } > CHILD3

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
