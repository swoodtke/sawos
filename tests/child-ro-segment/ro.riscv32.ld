/* SOS test child linker script: riscv32, read-only data above the data.
 *
 * `hal/riscv32/user/child.ld`'s addresses and arena, with the read-only data
 * linked after the writable data and its zero-init instead of before them.
 * Three loadable segments as every image has (code R+X, data R+W, read-only
 * R), so the read-only one lies between the data and the stack, both of
 * which are destinations, and only its own row's permission makes it none.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

PHDRS {
    text PT_LOAD FLAGS(5);
    data PT_LOAD FLAGS(6);
    ro PT_LOAD FLAGS(4);
}

MEMORY {
    CHILD (rwxa) : ORIGIN = 0x80240000, LENGTH = 240K
}

SECTIONS {
    . = 0x80240000;

    .text : {
        *(.text .text.*)
        . = ALIGN(4);
    } > CHILD :text

    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD :data

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD :data

    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD :ro

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
