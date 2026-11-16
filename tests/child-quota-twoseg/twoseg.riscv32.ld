/* `hal/riscv32/user/user.ld` with `.rodata` folded into `.text`, so the
 * image has two loadable segments (RX, RW) where every other test image has
 * three, and spends one grant row fewer. `quota_set_rows_child` needs a child
 * whose free rows differ from its launcher's. */

OUTPUT_ARCH(riscv)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

MEMORY {
    CHILD (rwxa) : ORIGIN = SOS_REGION_BASE, LENGTH = SOS_REGION_LEN - SOS_STACK_LEN
}

SECTIONS {
    . = SOS_REGION_BASE;

    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
        _text_end = .;
    } > CHILD

    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
