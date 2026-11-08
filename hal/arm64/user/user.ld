/* SOS user image linker script: arm64.
 *
 * One script serves every process on this board. Every process runs under its
 * own translation-table set, so the whole low half is private to whichever
 * process is current, and the kernel maps each image's frames at this one
 * canonical base:
 *
 *   0x4020_0000  hal.USER_IMAGE_BASE  segments are linked from here
 *   0x4023_C000  stack bottom         the kernel grants this RW separately
 *   0x4024_0000  region top           initial sp, and the floor the kernel
 *                                     places mappings above
 *
 * Where the bytes actually go is not written here: the kernel copies each
 * segment to a frame in the destination region it was handed (root's is
 * `hal.ROOT_LOAD_BASE`, a child's is a `Memory` capability its launcher
 * supplied) and installs a grant row translating this virtual range to those
 * frames. `hal.image_link_base` answers "linked where"; this file is its arm64
 * answer written in the other language.
 *
 * LENGTH is 240K so the image stops exactly at the stack bottom, which keeps
 * the linker from placing data where the stack will live. Every process's
 * region is the same 256 KiB on this board (the child region length the
 * build's region table hands out), so one script fits root and child alike.
 *
 * Nothing at build time compares this base with `hal.USER_IMAGE_BASE`, since a
 * Saw static's value is not a linker symbol. The loader catches a
 * disagreement: `check_segment` validates every segment against the linked
 * region and refuses an image that does not fit, with a named diagnostic.
 *
 * Protection granularity here is the page. Two segments sharing a page would
 * be granted the union of their permissions, so `.text` and `.data` are each
 * page-aligned; otherwise a program's code could become writable because its
 * data happened to start 200 bytes later. The kernel's loader checks the
 * alignment rather than trusting the linker script.
 *
 * The riscv32 profile keeps a script per resident image
 * (`hal/riscv32/user/`): there an address is the same number in every process,
 * so two images at one base could not both be resident.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

/* The arena: `sosrt`'s bump-allocator region, which every freestanding Saw
 * image needs because the compiler's own panic path can allocate.
 *
 * `ARENA_SIZE` is the knob, defaulting to the `PROVIDE` below. A package that
 * wants another size writes a script of its own that assigns the symbol and
 * then INCLUDEs this file (`tests/arena-small/` is the worked example).
 * `PROVIDE` defines the symbol only when nothing else has, so the override wins
 * with no conditional. `rt/common_c/support.c` reads `_arena_start` and
 * `_arena_end` back, since Saw cannot name a linker symbol, and
 * `rt/common/src/lib.saw` allocates between them.
 *
 * It is `.bss`-resident, so it costs the image nothing and the process's
 * region everything.
 */
PROVIDE(ARENA_SIZE = 64K);

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
     * bytes are not in the image: the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > IMAGE

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
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
