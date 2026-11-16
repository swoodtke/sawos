/* `hal/arm64/user/user.ld` with `.rodata` folded into `.text`, so the image
 * has two loadable segments (RX, RW) where every other test image has three,
 * and spends one grant row fewer. `quota_set_rows_child` needs a child whose
 * free rows differ from its launcher's. */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

MEMORY {
    IMAGE (rwxa) : ORIGIN = 0x40200000, LENGTH = SOS_REGION_LEN - SOS_STACK_LEN
}

SECTIONS {
    . = 0x40200000;

    .text : ALIGN(4096) {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4096);
        _text_end = .;
    } > IMAGE

    .data : ALIGN(4096) {
        *(.data .data.*)
        . = ALIGN(16);
    } > IMAGE

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(4096);
    } > IMAGE

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
