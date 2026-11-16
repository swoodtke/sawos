/* SOS process linker script: riscv32, ESP32-C3 board.
 *
 * The same section layout as hal/riscv32/user/user.ld, which says how the
 * image builder links each process through it: a script of the process's own
 * assigns `SOS_REGION_BASE`, `SOS_REGION_LEN` and `SOS_STACK_LEN` and then
 * INCLUDEs this one. Keep the pair in step.
 *
 * Processes are copied into SRAM and run at the addresses they are linked
 * for, so each resident image is linked for its own region. Root's is the
 * HAL's (`ROOT_LOAD_BASE` to `ROOT_REGION_TOP`, ../kernel/lib.saw). The region
 * is small on this part (../kernel/esp32c3.ld says why), and its LENGTH stops
 * at the stack bottom, so an image too big for it is a link error.
 *
 * Sections are grouped so the image needs few loadable segments: each costs a
 * PMP region, and the budget is small. Three load: code (R+X), read-only data
 * (R) and writable data (R+W). Read-only data is not in the executable segment
 * because a system call's buffer may not lie in memory the process can
 * execute, and a string literal is a buffer.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

/* The arena: `sosrt`'s bump-allocator region, which every freestanding Saw
 * image needs because the compiler's own panic path can allocate.
 *
 * `ARENA_SIZE` is the knob, defaulting to the `PROVIDE` below. A package that
 * wants another size writes a script of its own that assigns the symbol and
 * then INCLUDEs this file (`tests/arena-small/` is the worked example).
 * `PROVIDE` defines the symbol only when nothing else has, so the override
 * wins.
 * `rt/common_c/support.c` reads `_arena_start` and `_arena_end` back (Saw
 * cannot name a linker symbol) and `rt/common/src/lib.saw` allocates between
 * them.
 *
 * The arena is `.bss`-resident, so it costs nothing in the image but counts
 * fully against the process's region, which is small on this part: size it
 * with the region in mind.
 */
PROVIDE(ARENA_SIZE = 64K);

MEMORY {
    REGION (rwxa) : ORIGIN = SOS_REGION_BASE, LENGTH = SOS_REGION_LEN - SOS_STACK_LEN
}

SECTIONS {
    . = SOS_REGION_BASE;

    /* Code: one R+X segment. `_text_end` is the first address past it. */
    .text : {
        *(.text .text.*)
        . = ALIGN(4);
        _text_end = .;
    } > REGION

    /* Read-only data: a segment of its own, readable and neither
     * writable nor executable, so a string literal is no part of
     * executable memory. */
    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > REGION

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image: the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > REGION

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > REGION

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
