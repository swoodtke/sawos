/* SOS user image linker script: arm64.
 *
 * One script serves every process on this board. Every process runs under its
 * own translation-table set, so the whole low half is private to whichever
 * process is current, and the kernel maps each image's frames at this one
 * canonical base:
 *
 *   0x4020_0000           segments are linked from here (hal.USER_IMAGE_BASE)
 *   region top - stack    stack bottom: the kernel grants this RW separately
 *   region top            initial sp, and the floor the kernel places
 *                         mappings above
 *
 * where the region top is the base plus the process's region length.
 *
 * Where the bytes actually go is not written here: the kernel copies each
 * segment to a frame in the destination region it was handed (root's is
 * `hal.ROOT_LOAD_BASE`, a child's is a `Memory` capability its launcher
 * supplied) and installs a grant row translating this virtual range to those
 * frames. `hal.image_link_base` answers "linked where"; this file is its arm64
 * answer written in the other language.
 *
 * The image builder links each process with a script of its own that assigns
 * `SOS_REGION_BASE`, `SOS_REGION_LEN` and `SOS_STACK_LEN` and then INCLUDEs
 * the script the package's manifest names, this one or a package's own that
 * INCLUDEs it (`sosimage package`, `tools/sosimage/src/layout.saw`). Only the
 * region's length matters here: the base is the frames', which no program
 * sees. LENGTH stops at the stack bottom, which keeps the linker from placing
 * data where the stack will live.
 *
 * Nothing at build time compares this base with `hal.USER_IMAGE_BASE`, since a
 * Saw static's value is not a linker symbol. The loader catches a
 * disagreement: `check_segment` validates every segment against the linked
 * region and refuses an image that does not fit, with a named diagnostic.
 *
 * Protection granularity here is the page. Two segments sharing a page would
 * be granted the union of their permissions, so `.text`, `.rodata` and `.data`
 * are each page-aligned; otherwise a program's code could become writable
 * because its data happened to start 200 bytes later. The kernel's loader
 * checks the alignment rather than trusting the linker script.
 *
 * On the riscv32 board an address is the same number in every process, so
 * there the builder links each resident image for its own region
 * (`hal/riscv32/user/user.ld`); here every image shares this base.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

/* The arena: the region `sosrt`'s heap serves a freestanding Saw image's
 * allocations from.
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
 *
 * The default is small. Start-up, `print` and `panic` with `{}` arguments
 * and the checks the compiler inserts allocate nothing, so the arena serves
 * only what the program itself allocates: an interpolated string, a `String`,
 * a growing `Vector`. A process that needs more says so with `arena` in its
 * `process.toml` (`tools/sosimage/src/declaration.saw`), or with a script of
 * its own as above.
 */
PROVIDE(ARENA_SIZE = 4K);

MEMORY {
    IMAGE (rwxa) : ORIGIN = 0x40200000, LENGTH = SOS_REGION_LEN - SOS_STACK_LEN
}

SECTIONS {
    . = 0x40200000;

    /* Code: one R+X segment, page-aligned at both ends. `_text_end` is the
     * first address past it. */
    .text : ALIGN(4096) {
        *(.text .text.*)
        . = ALIGN(4096);
        _text_end = .;
    } > IMAGE

    /* Read-only data: a segment of its own, readable and neither writable nor
     * executable, page-aligned at both ends, so a string literal is no part of
     * executable memory. */
    .rodata : ALIGN(4096) {
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
