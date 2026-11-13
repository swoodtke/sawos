/* SOS test child linker script: riscv32, one writable and executable segment.
 *
 * `hal/riscv32/user/child.ld`'s addresses and arena, with every section in ONE
 * loadable segment that is readable, writable and executable (`FLAGS(7)`),
 * which the loader accepts (spec §2.5). Its write bit puts the segment inside
 * the copy-out window, so only the execute rule keeps a buffer out of it.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

PHDRS {
    all PT_LOAD FLAGS(7);
}

MEMORY {
    CHILD (rwxa) : ORIGIN = 0x80240000, LENGTH = 240K
}

SECTIONS {
    . = 0x80240000;

    .text : {
        *(.text .text.*)
        . = ALIGN(4);
    } > CHILD :all

    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD :all

    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD :all

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD :all

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
