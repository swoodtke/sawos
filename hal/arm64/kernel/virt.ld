/* SOS kernel linker script (design 162) — QEMU `virt`, arm64, EL1.
 *
 * RAM at 0x4000_0000 (where QEMU places an ELF given with `-kernel`).
 * `.text.boot` is placed first so the ELF entry (`_start`) is the reset target.
 * Per-function / per-data sections (`.text.*`, `.rodata.*`, `.data.*`, `.bss.*`
 * — emitted by sawc's freestanding profile and by
 * `clang -ffunction-sections -fdata-sections`) are caught by the wildcards
 * below and trimmed with `ld.lld --gc-sections`. `@section` placements still
 * work: a named section not matched here would just need its own entry.
 *
 * ---------------------------------------------------------------------------
 * PHYSICAL MEMORY MAP (Profile B, design 162)
 * ---------------------------------------------------------------------------
 *   0x0800_0000  GIC distributor / CPU interface  (never enabled in M1)
 *   0x0900_0000  PL011 UART                       (EL1 only, never granted)
 *   0x4000_0000  kernel image: .text .rodata .data .payload, then .bss
 *                (64 KiB stack and the three page tables live in .bss)
 *   0x4020_0000  ROOT REGION BASE  — 2 MiB in, well clear of the kernel image,
 *                and a level-2 boundary so root's pages come out of the grant
 *                window whole; root's segments load here
 *   0x4023_C000  root stack bottom
 *   0x4024_0000  ROOT REGION TOP   (256 KiB region, 16 KiB stack)
 *
 * The root numbers are NOT set here — the HAL's `lib.saw` and the root linker
 * script own them, and the kernel validates every segment against the region
 * before placing it. They are recorded here because this is the file that fixes
 * the kernel side of the map.
 *
 * ---------------------------------------------------------------------------
 * PROTECTION LAYOUT (design 162 decision 2)
 * ---------------------------------------------------------------------------
 * EL0 is default-deny: the identity map built in `sink.c` gives EL0 nothing at
 * all until a grant says otherwise, so the kernel, the UART and the GIC are
 * protected by the kernel simply never granting them. The kernel's own pages
 * are EL1 read/write/execute — the arm64 spelling of Profile A's unconstrained
 * M-mode.
 *
 * The GRANT WINDOW is the first 4 MiB of RAM at page granularity. It has to
 * cover the kernel image as well as the root region, because the harness's
 * unit-A cases grant a payload that executes IN PLACE inside the appended
 * `.payload` section.
 */

OUTPUT_ARCH(aarch64)
ENTRY(_start)

MEMORY {
    RAM (rwxa) : ORIGIN = 0x40000000, LENGTH = 128M
}

SECTIONS {
    . = 0x40000000;

    .text : {
        KEEP(*(.text.boot))
        KEEP(*(.text.vectors))
        *(.text .text.*)
    } > RAM

    .rodata : {
        *(.rodata .rodata.*)
    } > RAM

    .data : {
        *(.data .data.*)
    } > RAM

    /* The appended root image (design 140 unit B): a blob the harness pulls in
     * with `.incbin`, bounded by linker symbols the kernel reads through the
     * kernel HAL's `sos_payload_start`/`sos_payload_end` accessors (sink.c —
     * Saw cannot name a linker symbol). Unit A's user-mode payloads ride the
     * same slot as raw code, which is why this section is executable. Empty
     * (start == end) in an image built without a payload.
     *
     * PAGE-aligned at both ends, and on this profile that is load-bearing
     * rather than tidy: protection granularity here IS the page, so a payload
     * sharing a page with kernel code would grant EL0 that code. Rounding the
     * end up as well is what keeps the grant from spilling into `.bss`. */
    .payload : ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        . = ALIGN(4096);
        _payload_end = .;
    } > RAM

    .bss (NOLOAD) : ALIGN(4096) {
        _bss_start = .;
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _bss_end = .;
    } > RAM

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }
}
