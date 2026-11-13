/* SOS root server linker script: riscv32.
 *
 * Root is loaded by the kernel at a fixed address: there is no relocation and
 * no MMU on this profile, so the addresses here and the root-region constants
 * in the HAL's `lib.saw` are one decision recorded twice. The kernel validates
 * every segment against the region and rejects an image that does not fit, so
 * a mismatch is a clean diagnostic rather than a corrupted kernel.
 *
 *   0x8020_0000  ROOT_LOAD_BASE   segments load from here
 *   0x8023_C000  stack bottom     the kernel grants this RW separately
 *   0x8024_0000  ROOT_REGION_TOP  initial sp
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live.
 *
 * Sections are grouped so the image needs few loadable segments: each one
 * costs a PMP region, and the kernel's budget is small (see virt.ld). Three
 * load: code (R+X), read-only data (R) and writable data (R+W). Read-only data
 * is not in the executable segment because a system call's buffer may not lie
 * in memory the process can execute, and a string literal is a buffer.
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
    ROOT (rwxa) : ORIGIN = 0x80200000, LENGTH = 240K
}

SECTIONS {
    . = 0x80200000;

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
