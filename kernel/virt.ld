/* SOS M0 linker script (design 112) — QEMU `virt`, riscv32.
 *
 * RAM at 0x8000_0000 (where QEMU jumps with `-bios none -kernel`). `.text.boot`
 * is placed first so the ELF entry (`_start`) is the reset target. Per-function
 * / per-data sections (`.text.*`, `.rodata.*`, `.data.*`, `.bss.*` — emitted by
 * sawc's freestanding profile and by `clang -ffunction-sections -fdata-sections`)
 * are caught by the wildcards below and trimmed with `ld.lld --gc-sections`.
 * `@section` placements still work: a named section not matched here would just
 * need its own output-section entry.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
    RAM (rwxa) : ORIGIN = 0x80000000, LENGTH = 128M
}

SECTIONS {
    . = 0x80000000;

    .text : {
        KEEP(*(.text.boot))
        *(.text .text.*)
    } > RAM

    .rodata : {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
    } > RAM

    .data : {
        *(.data .data.*)
        *(.sdata .sdata.*)
    } > RAM

    .bss (NOLOAD) : {
        _bss_start = .;
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _bss_end = .;
    } > RAM

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
