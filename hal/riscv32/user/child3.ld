/* SOS third-child process linker script: riscv32.
 *
 * `child2.ld` with one number changed. Root plus two children fills the
 * compiled `MAX_PROCESSES` slots, so a third child alive at the same time is a
 * process in a donated slot. On this profile it needs its own load address like
 * every other image, because there is no translation here: a child image runs
 * at exactly the base it was linked at (`hal.image_link_base` is the
 * identity).
 *
 *   0x802C_0000  CHILD3_LOAD_BASE   one region above the second child's
 *   0x802F_C000  stack bottom       the kernel grants this RW separately
 *   0x8030_0000  CHILD3_REGION_TOP  initial sp (256 KiB region, 16 KiB stack)
 *
 * The base is `tools/sos_runner.py`'s arithmetic, not a second opinion:
 * `child_region_base + 2 * CHILD_REGION_LEN`. A package linked with this file
 * is a package the case must list third, the same index coupling `child.ld`
 * and `child2.ld` have.
 *
 * The arm64 side needs no such file: every arm64 user image links at one
 * canonical base (`hal/arm64/user/user.ld`) and the kernel translates it to
 * whichever frames the destination region occupies, so one script serves any
 * number of concurrent children there. A fourth concurrent child on this
 * profile would want a fourth script.
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
     * bytes are not in the image: the sosimg segment's mem_len exceeds its
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
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD3

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
