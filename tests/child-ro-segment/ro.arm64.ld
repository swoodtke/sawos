/* SOS test child linker script: arm64, read-only data above the data.
 *
 * `hal/arm64/user/user.ld`'s addresses and arena, with the read-only data
 * linked after the writable data and its zero-init instead of before them,
 * each segment page-aligned at both ends. Three loadable segments as every
 * image has (code R+X, data R+W, read-only R), so the read-only one lies
 * between the data and the stack, both of which are destinations, and only its
 * own row's permission makes it none.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

PROVIDE(ARENA_SIZE = 64K);

PHDRS {
    text PT_LOAD FLAGS(5);
    data PT_LOAD FLAGS(6);
    ro PT_LOAD FLAGS(4);
}

MEMORY {
    IMAGE (rwxa) : ORIGIN = 0x40200000, LENGTH = 240K
}

SECTIONS {
    . = 0x40200000;

    .text : ALIGN(4096) {
        *(.text .text.*)
        . = ALIGN(4096);
    } > IMAGE :text

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
