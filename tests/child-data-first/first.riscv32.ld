/* SOS test child linker script: riscv32, writable data first.
 *
 * `hal/riscv32/user/child2.ld`'s addresses and arena, with the writable data
 * and its zero-init linked at the base and the code and read-only data after
 * them. The writable segment is the image's first, so it is the first grant
 * row the loader records for the process. `_image_start` and `_text_start`
 * mark the base and the code's first byte.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

PHDRS {
    data PT_LOAD FLAGS(6);
    text PT_LOAD FLAGS(5);
    ro PT_LOAD FLAGS(4);
}

MEMORY {
    CHILD2 (rwxa) : ORIGIN = 0x80280000, LENGTH = 240K
}

SECTIONS {
    . = 0x80280000;
    _image_start = .;

    .data : {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD2 :data

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD2 :data

    .text : ALIGN(16) {
        _text_start = .;
        *(.text .text.*)
        . = ALIGN(4);
    } > CHILD2 :text

    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD2 :ro

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
