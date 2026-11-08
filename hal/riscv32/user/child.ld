/* SOS child process linker script: riscv32.
 *
 * Root's script with one number changed. Under the PMP no-translation contract
 * every process shares one physical address space (spec §5.5), so two images
 * cannot both be linked at `ROOT_LOAD_BASE`. Each child is linked at a distinct
 * base assigned in the build's config, and `process_create` validates every
 * segment against the destination region root hands it.
 *
 *   0x8024_0000  CHILD_LOAD_BASE   one region above root's, and the base of
 *                                  the free-RAM row the harness's region table
 *                                  publishes
 *   0x8027_C000  stack bottom      the kernel grants this RW separately
 *   0x8028_0000  CHILD_REGION_TOP  initial sp (256 KiB region, 16 KiB stack)
 *
 * The base is `ROOT_REGION_TOP` exactly: the simplest assignment disjoint from
 * everything else. Root's region ends there and the kernel image and its blobs
 * are two megabytes below. A second child takes the next 256 KiB, and the
 * config, not this file, says so.
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live. Sections are grouped so the image needs few
 * loadable segments, for the reason root's script gives: each one costs a PMP
 * region, and the reload at a context switch installs one process's rows at a
 * time.
 */

OUTPUT_ARCH(riscv)
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
    CHILD (rwxa) : ORIGIN = 0x80240000, LENGTH = 240K
}

SECTIONS {
    . = 0x80240000;

    /* Code + read-only data: one R+X segment. */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image: the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
