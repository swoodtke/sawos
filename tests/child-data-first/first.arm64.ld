/* SOS test child linker script: arm64, writable data first.
 *
 * `hal/arm64/user/user.ld`'s addresses and arena, with the writable data and
 * its zero-init linked at the base and the code and read-only data after
 * them, each segment page-aligned at both ends. The writable segment is the
 * image's first, so it is the first grant row the loader records for the
 * process, and the code begins on a page boundary inside the destination.
 * `_image_start` and `_text_start` mark the base and the code's first byte.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

PHDRS {
    data PT_LOAD FLAGS(6);
    text PT_LOAD FLAGS(5);
    ro PT_LOAD FLAGS(4);
}

MEMORY {
    IMAGE (rwxa) : ORIGIN = 0x40200000, LENGTH = 240K
}

SECTIONS {
    . = 0x40200000;
    _image_start = .;

    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > IMAGE :data

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(4096);
    } > IMAGE :data

    .text : ALIGN(4096) {
        _text_start = .;
        *(.text .text.*)
        . = ALIGN(4096);
    } > IMAGE :text

    .rodata : ALIGN(4096) {
        *(.rodata .rodata.*)
        . = ALIGN(4096);
    } > IMAGE :ro

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
