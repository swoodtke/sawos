/* SOS test child linker script: arm64, one writable and executable segment.
 *
 * `hal/arm64/user/user.ld`'s addresses and arena, with every section in ONE
 * loadable segment that is readable, writable and executable (`FLAGS(7)`),
 * which the loader accepts (spec §2.5). Its row permits read and write, so
 * only the execute rule keeps a buffer out of it.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

PHDRS {
    all PT_LOAD FLAGS(7);
}

MEMORY {
    IMAGE (rwxa) : ORIGIN = 0x40200000, LENGTH = 240K
}

SECTIONS {
    . = 0x40200000;

    .text : ALIGN(4096) {
        *(.text .text.*)
        . = ALIGN(16);
    } > IMAGE :all

    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
        . = ALIGN(16);
    } > IMAGE :all

    .data : ALIGN(16) {
        *(.data .data.*)
        . = ALIGN(16);
    } > IMAGE :all

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(4096);
    } > IMAGE :all

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
