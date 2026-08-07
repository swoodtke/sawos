/* SOS kernel linker script (designs 112, 140) — QEMU `virt`, riscv32.
 *
 * RAM at 0x8000_0000 (where QEMU jumps with `-bios none -kernel`). `.text.boot`
 * is placed first so the ELF entry (`_start`) is the reset target. Per-function
 * / per-data sections (`.text.*`, `.rodata.*`, `.data.*`, `.bss.*` — emitted by
 * sawc's freestanding profile and by `clang -ffunction-sections -fdata-sections`)
 * are caught by the wildcards below and trimmed with `ld.lld --gc-sections`.
 * `@section` placements still work: a named section not matched here would just
 * need its own output-section entry.
 *
 * ---------------------------------------------------------------------------
 * PHYSICAL MEMORY MAP (Profile A, design 140 pin)
 * ---------------------------------------------------------------------------
 *   0x0010_0000  sifive_test finisher       (M-mode only, never granted)
 *   0x1000_0000  NS16550A UART              (M-mode only, never granted)
 *   0x8000_0000  kernel image: .text .rodata .data .payload, then .bss
 *                (64 KiB stack + the 128-byte trap frame live in .bss)
 *   0x8020_0000  ROOT REGION BASE           — 2 MiB in, well clear of the
 *                kernel image; root's segments load here
 *   0x8023_C000  root stack bottom
 *   0x8024_0000  ROOT REGION TOP            (256 KiB region, 16 KiB stack)
 *
 * The root numbers are NOT set here — root.ld and the kernel's loader own them,
 * and the kernel validates every segment against the region before placing it.
 * They are recorded here because this is the file that fixes the kernel side of
 * the map.
 *
 * ---------------------------------------------------------------------------
 * PMP REGION LAYOUT (design 140 pin)
 * ---------------------------------------------------------------------------
 * U-mode is default-deny: RISC-V fails any U-mode access that matches no PMP
 * entry, so the kernel, the UART and the finisher are protected by the kernel
 * simply never granting them. M-mode is unconstrained (SOS sets no L bits).
 *
 * Regions are TOR pairs — entry 2*i is the lower bound (A=OFF, a bound only),
 * entry 2*i+1 is the upper bound (A=TOR) and carries R/W/X. Four regions, eight
 * of QEMU's sixteen entries:
 *
 *   region 0..N-1  one per sosimg segment, permissions from the segment flags
 *   region N       the root stack, RW
 *
 * A sosimg with more segments than the budget allows is rejected as a malformed
 * image rather than being silently under-protected.
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

    /* The appended root image (design 140 unit B): a blob the harness pulls in
     * with `.incbin`, bounded by linker symbols the kernel reads through the
     * kernel HAL's `sos_payload_start`/`sos_payload_end` accessors (sink.c —
     * Saw cannot name a linker symbol). Unit A's U-mode payloads ride the same
     * slot as raw code, which
     * is why this section is executable. Empty (start == end) in an image built
     * without a payload. A real flash partition table is P4-hardware material. */
    /* PAGE-aligned, not merely word-aligned. Two reasons, one of them found the
     * hard way: an image blob is a page-granular thing everywhere else it will
     * ever live (a flash partition, a mapped MemoryObject), and a payload that
     * STRADDLES a page boundary is a genuinely awkward object — with the C
     * extension enabled a 4-byte instruction can begin at a 2-mod-4 address and
     * span two pages, which QEMU 11.0.3 does not merely fault on but asserts
     * inside its own translator. Starting the section on a page keeps a small
     * payload entirely within one. */
    .payload : ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        _payload_end = .;
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
