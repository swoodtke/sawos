/* SOS user image linker script (design 162) — arm64, Profile B.
 *
 * **ONE SCRIPT FOR EVERY PROCESS ON THIS BOARD** (sawos design 33, M5 unit 2).
 * It replaces `root.ld`, `child.ld` and `child2.ld`, which differed from each
 * other in exactly one number — the base — and existed only because a user
 * address used to BE a physical address, so two resident images could not share
 * one.
 *
 * They can now. Every process runs under its own translation-table set, so the
 * whole low half is private to whichever process is current, and the kernel maps
 * each image's frames at this one canonical base:
 *
 *   0x4020_0000  hal.USER_IMAGE_BASE  — segments are LINKED from here
 *   0x4023_C000  stack bottom         — the kernel grants this RW separately
 *   0x4024_0000  region top           — initial sp, and the floor the kernel
 *                                       places mappings above
 *
 * WHERE THE BYTES ACTUALLY GO is no longer written down here at all: the kernel
 * copies each segment to a frame in the destination region it was handed
 * (root's is `hal.ROOT_LOAD_BASE`, a child's is a `Memory` capability its
 * launcher supplied) and installs a grant row translating this virtual range to
 * those frames. `hal.image_link_base` is the seam that answers "linked where",
 * and this file is its arm64 answer written in the other language.
 *
 * LENGTH is 240K so it stops exactly at the stack bottom, which keeps the linker
 * from placing data where the stack will live. It also means the SIZE of every
 * process's region is the same 256 KiB on this board — the child region length
 * the build's region table hands out — so one script fits root and child alike.
 *
 * NOTHING AT BUILD TIME COMPARES THIS BASE WITH `hal.USER_IMAGE_BASE`: a Saw
 * static's value is not a linker symbol (design 29 finding 5). What catches a
 * disagreement is the loader — `check_segment` validates every segment against
 * the linked region and refuses an image that does not fit, with a named
 * diagnostic rather than a silent misplacement.
 *
 * WHAT DIFFERS FROM PROFILE A, and it is the whole reason this file is not a
 * copy: protection granularity here is the PAGE. Two segments sharing a page
 * would be granted the union of their permissions, so `.text` and `.data` are
 * each page-aligned — which is what keeps a program's code from becoming
 * writable because its data happened to start 200 bytes later. Profile A grants
 * at four-byte granularity and needs none of this; the kernel's loader checks
 * the alignment either way rather than assuming a linker script got it right.
 *
 * AND PROFILE A KEEPS ITS THREE SCRIPTS, deliberately (design 25 ruling 11).
 * There an address is still the same number in every process, so two images at
 * one base could not both be resident — `hal/riscv32/user/` still carries
 * `root.ld`, `child.ld` and `child2.ld`, and that pair of directories is the
 * tier split as a thing you can look at.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

MEMORY {
    IMAGE (rwxa) : ORIGIN = 0x40200000, LENGTH = 240K
}

SECTIONS {
    . = 0x40200000;

    /* Code + read-only data: one R+X segment, page-aligned at both ends. */
    .text : ALIGN(4096) {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4096);
    } > IMAGE

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > IMAGE

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(4096);
    } > IMAGE

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
