/* SOS process linker script: riscv32, the QEMU `virt` board.
 *
 * One script for every process on this board, root included. It names no
 * address: the image builder links each process with a script of its own that
 * assigns three symbols and then INCLUDEs the script the package's manifest
 * names, this one or a package's own that INCLUDEs it (`sosimage package`,
 * `tools/sosimage/src/layout.saw`):
 *
 *   SOS_REGION_BASE  where the process's region begins: its segments load
 *                    from here
 *   SOS_REGION_LEN   the region's length, its stack included
 *   SOS_STACK_LEN    the stack at the region's top, which the kernel grants
 *                    RW separately; the initial sp is the region's top
 *
 * This profile does not translate: a process runs at exactly the addresses it
 * is linked for, and every process shares one physical address space (spec
 * §5.5). So each resident image is linked for its own region, and the builder
 * places the regions: root's is the HAL's (`ROOT_LOAD_BASE` to
 * `ROOT_REGION_TOP`), and the kernel validates every segment against the
 * region it is loaded into, so a mismatch is a clean diagnostic rather than a
 * corrupted kernel.
 *
 * The region's LENGTH stops exactly at the stack bottom, so the linker cannot
 * place data where the stack will live; an image too big for its region is a
 * link error naming the section.
 *
 * Sections are grouped so the image needs few loadable segments: each one
 * costs a PMP region, and the kernel's budget is small (see
 * ../kernel/virt.ld). Three load: code (R+X), read-only data (R) and writable
 * data (R+W). Read-only data is not in the executable segment because a
 * system call's buffer may not lie in memory the process can execute, and a
 * string literal is a buffer.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)
/* A program written with `main` gets its `_start` from `sosmain`, and only
 * when it imports that package. Without the import the link has no entry,
 * which `ld.lld` only warns about, so the warning is made an error that names
 * the fix.
 */
ASSERT(DEFINED(_start), "no _start: a main program imports sosmain")

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
