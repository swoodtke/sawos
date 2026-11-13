/* SOS root server linker script: Xtensa, ESP32-S3 board.
 *
 * The section layout of hal/riscv32-esp32c3/user/root.ld, with every literal
 * pool ahead of the code that loads it (`l32r` reaches only backwards, up to
 * 256 KiB; ../ABI.md §1c) and this board's addresses.
 *
 * Root is loaded by the kernel at a fixed address: nothing relocates it and
 * nothing translates, so the addresses here and the root-region constants in
 * `../kernel/lib.saw` are one decision recorded twice. The kernel validates
 * every segment against the region and refuses an image that does not fit.
 *
 *   0x403A_0000  ROOT_LOAD_BASE   - segments load from here
 *   0x403B_4000  stack bottom     - the kernel grants the stack separately
 *   0x403B_8000  ROOT_REGION_TOP  - initial a1
 *
 * The region is in IRAM, where code can execute on silicon as well as on the
 * emulator, and is the twin of DRAM the kernel never uses (see
 * `../kernel/esp32s3.ld`). LENGTH stops at the stack bottom, so the linker
 * cannot place data where the stack will live.
 *
 * Code, literals and read-only data share one segment, and writable data and
 * zero-init another, as on the riscv32 boards.
 */

ENTRY(_start)

/* `sosrt`'s bump-allocator arena (hal/riscv32/user/root.ld describes the
 * knob). `.bss`-resident, so it costs nothing in the image but counts fully
 * against the region, which is small on this part: what is left of LENGTH
 * after the arena is the image's. */
PROVIDE(ARENA_SIZE = 48K);

MEMORY {
    ROOT (rwxa) : ORIGIN = 0x403A0000, LENGTH = 80K
}

SECTIONS {
    . = 0x403A0000;

    .text : {
        *(.literal .literal.*)
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > ROOT

    .data : ALIGN(16) {
        *(.data .data.*)
        . = ALIGN(4);
    } > ROOT

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > ROOT

    /DISCARD/ : {
        *(.comment)
        *(.xtensa.info)
        *(.xt.prop .xt.prop.*)
        *(.xt.lit .xt.lit.*)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
