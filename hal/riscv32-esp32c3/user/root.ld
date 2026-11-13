/* SOS root server linker script: riscv32, ESP32-C3 board.
 *
 * Same section layout as hal/riscv32/user/root.ld; only the addresses differ,
 * to fit the C3's SRAM budget (see ../kernel/esp32c3.ld). Keep the pair in
 * step.
 *
 * Root is loaded by the kernel at a fixed address: there is no relocation and
 * no MMU on this profile, so the addresses here and the root-region constants
 * in the HAL are one decision recorded twice. The kernel validates every
 * segment against the region and rejects an image that does not fit, so a
 * mismatch is a clean diagnostic rather than a corrupted kernel.
 *
 *   0x403A_6000  ROOT_LOAD_BASE   - segments load from here
 *   0x403C_1000  stack bottom     - the kernel grants this RW separately
 *   0x403C_5000  ROOT_REGION_TOP  - initial sp
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live.
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
    ROOT (rwxa) : ORIGIN = 0x403A6000, LENGTH = 108K
}

SECTIONS {
    . = 0x403A6000;

    /* Code: one R+X segment. */
    .text : {
        *(.text .text.*)
        . = ALIGN(4);
    } > ROOT

    /* Read-only data: a segment of its own, readable and neither
     * writable nor executable, so a string literal is no part of
     * executable memory. */
    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > ROOT

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image: the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > ROOT

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > ROOT

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
