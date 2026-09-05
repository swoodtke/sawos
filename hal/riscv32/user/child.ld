/* SOS CHILD process linker script (sawos design 2) — riscv32, Profile A.
 *
 * ROOT'S SCRIPT WITH ONE NUMBER CHANGED, and the number is the whole design.
 * Under the PMP no-translation contract every process shares one physical
 * address space (spec §5.5), so two images cannot both be linked at
 * `ROOT_LOAD_BASE` — each child is linked at a DISTINCT base assigned in the
 * build's config, and `process_create` validates every segment against the
 * destination region root hands it.
 *
 *   0x8024_0000  CHILD_LOAD_BASE  — one region above root's, and the base of
 *                the free-RAM row the harness's region table publishes
 *   0x8027_C000  stack bottom     — the kernel grants this RW separately
 *   0x8028_0000  CHILD_REGION_TOP — initial sp (256 KiB region, 16 KiB stack)
 *
 * THE BASE IS `ROOT_REGION_TOP` EXACTLY, which is not a coincidence to be
 * relied on so much as the simplest assignment that is disjoint from everything
 * else: root's region ends there, the kernel image and its blobs are two
 * megabytes below, and on the other profile the same offset also keeps the
 * region inside that HAL's 4 MiB EL0 grant window. A second child would take
 * the next 256 KiB, and the config — not this file — is what would say so.
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live. Sections are grouped so the image needs FEW
 * loadable segments, for the reason root's script gives: each one costs a PMP
 * region out of a budget of four, and the reload at a context switch installs
 * one process's rows at a time.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

/* THE ARENA (sawos design 38) — `sosrt`'s bump-allocator region, which every
 * freestanding Saw image needs because the compiler's own panic path can
 * allocate.
 *
 * `ARENA_SIZE` IS THE KNOB, and 64 KiB is what a package that says nothing
 * gets. A package that wants another size writes a script of its own that
 * assigns the symbol and then INCLUDEs this file (`tests/arena-small/` is the
 * worked example); `PROVIDE` is what makes that work, since it defines the
 * symbol only when nothing else has, so the override wins with no conditional
 * anywhere. `rt/common_c/support.c` reads `_arena_start` and `_arena_end` back
 * — Saw cannot name a linker symbol — and `rt/common/src/lib.saw` allocates
 * between them; see that file for why the arena exists at all.
 *
 * It is `.bss`-resident, so it costs the IMAGE nothing and the process's
 * REGION everything: design 20 finding 4 measured 65,536 bytes of arena in a
 * 67,600-byte child, on a part with 400 KiB of SRAM.
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
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
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
