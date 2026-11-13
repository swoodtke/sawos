/* SOS child process linker script: Xtensa, ESP32-S3 board.
 *
 * Root's script (`root.ld`) with a different base. Every process shares one
 * address space on this tier (spec §5.5), so two images cannot both be linked
 * at `ROOT_LOAD_BASE`. A child is linked at the base of the destination region
 * the build's region table publishes (the runner's `child_region_base` for
 * this board), and `process_create` validates every segment against the
 * destination region root hands it.
 *
 *   0x403B_8000  child base       - one region above root's
 *   0x403C_C000  stack bottom     - the kernel grants the stack separately
 *   0x403D_0000  region top       - initial a1; the RAM pool starts here
 *
 * LENGTH stops at the stack bottom, so the linker cannot place data where the
 * stack will live. A second resident child would need a script of its own at
 * another base; no case on this board has one.
 */

ENTRY(_start)

PROVIDE(ARENA_SIZE = 48K);

MEMORY {
    CHILD (rwxa) : ORIGIN = 0x403B8000, LENGTH = 80K
}

SECTIONS {
    . = 0x403B8000;

    .text : {
        *(.literal .literal.*)
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > CHILD

    .data : ALIGN(16) {
        *(.data .data.*)
        . = ALIGN(4);
    } > CHILD

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD

    /DISCARD/ : {
        *(.comment)
        *(.xtensa.info)
        *(.xt.prop .xt.prop.*)
        *(.xt.lit .xt.lit.*)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
