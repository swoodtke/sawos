/* SOS second-child process linker script: riscv32.
 *
 * `child.ld` with one number changed, for a case with two children alive at
 * once (a driver child and a client child).
 *
 *   0x8028_0000  CHILD2_LOAD_BASE   one region above the first child's
 *   0x802B_C000  stack bottom       the kernel grants this RW separately
 *   0x802C_0000  CHILD2_REGION_TOP  initial sp (256 KiB region, 16 KiB stack)
 *
 * The base is `tools/sos_runner.py`'s arithmetic, not a second opinion:
 * `child_region_base + 1 * CHILD_REGION_LEN`. A package's manifest picks a
 * script and the runner's `_region_rows` assigns destinations by index, so a
 * package linked with this file is a package the case must list second.
 *
 * It collides with the RAM pool a `"pool": True` case publishes, which starts
 * at the same address. A case cannot have two children and a pool;
 * `_region_rows` refuses that combination rather than emitting a table whose
 * rows overlap.
 *
 * Everything else (the section grouping, the `MEMORY` length that stops at the
 * stack bottom, the discarded notes) is `child.ld`'s, for `child.ld`'s reasons.
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
    CHILD2 (rwxa) : ORIGIN = 0x80280000, LENGTH = 240K
}

SECTIONS {
    . = 0x80280000;

    /* Code + read-only data: one R+X segment. */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD2

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image: the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD2

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD2

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
