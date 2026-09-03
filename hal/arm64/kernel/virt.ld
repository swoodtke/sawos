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

/* ---------------------------------------------------------------------------
 * THE HIGHER-HALF LINK (sawos design 29, M5 unit 1.5)
 * ---------------------------------------------------------------------------
 * The kernel LINKS at high virtual addresses and LOADS at the physical ones it
 * always loaded at. Every section below is placed at `physical + LINMAP_OFFSET`
 * and given an explicit load address of `ADDR(section) - LINMAP_OFFSET`, so:
 *
 *   - the ELF's PROGRAM HEADERS carry the same physical addresses as before,
 *     and QEMU (which loads `-kernel` at each segment's `p_paddr`) puts the
 *     image exactly where it used to;
 *   - every symbol the kernel resolves at run time is a high address, which is
 *     what makes the kernel reachable through TTBR1 and TTBR0 purely the user's.
 *
 * `AT(ADDR(.x) - LINMAP_OFFSET)` per section rather than a second MEMORY region:
 * with two regions the VMA and LMA cursors advance independently, so a section
 * whose alignment differs from its predecessor's silently breaks the fixed
 * delta the whole scheme rests on. Spelling the load address from the virtual
 * one makes the delta an identity instead of a coincidence.
 *
 * THE ENTRY POINT IS PHYSICAL. QEMU sets the reset PC from `e_entry` with the
 * MMU off, so a high entry would fault before the first instruction. `_start`
 * itself stays a high symbol like everything else; `_start_phys` below is its
 * load address, and that is what `ENTRY` names.
 *
 * THE OFFSET IS ALSO IN `hal/arm64/kernel/lib.saw` (`LINMAP_OFFSET`), and
 * nothing at build time can compare the two — a Saw static's VALUE is not a
 * linker symbol. `linmap_offset_probe` compares them AT BOOT, against the
 * address this script actually gave a static that file owns, before the linear
 * map is built. `LINMAP_PHYS_MASK` is exported for `boot.S`, which needs the
 * complement as a value it can load; the two ASSERTs below are what keep this
 * file's own pair consistent.
 */
LINMAP_OFFSET    = 0xFFFFFF8000000000;
LINMAP_PHYS_MASK = 0x0000007FFFFFFFFF;
ASSERT((LINMAP_OFFSET & LINMAP_PHYS_MASK) == 0,
       "the linear-map offset must not overlap the physical half")
ASSERT((LINMAP_OFFSET | LINMAP_PHYS_MASK) == 0xFFFFFFFFFFFFFFFF,
       "the linear-map offset and mask must partition the 64-bit address")

OUTPUT_ARCH(aarch64)
ENTRY(_start_phys)

MEMORY {
    RAM (rwxa) : ORIGIN = 0x40000000 + 0xFFFFFF8000000000, LENGTH = 128M
}

SECTIONS {
    . = 0x40000000 + LINMAP_OFFSET;

    .text : AT(ADDR(.text) - LINMAP_OFFSET) {
        KEEP(*(.text.boot))
        KEEP(*(.text.vectors))
        *(.text .text.*)
    } > RAM

    /* The ELF entry: `_start`'s LOAD address, which is where the reset PC has to
     * land because the MMU is off there. `boot.S` runs a handful of instructions
     * at this address and then jumps to its own high half. */
    _start_phys = _start - LINMAP_OFFSET;

    .rodata : AT(ADDR(.rodata) - LINMAP_OFFSET) {
        *(.rodata .rodata.*)
    } > RAM

    .data : AT(ADDR(.data) - LINMAP_OFFSET) {
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
    .payload : AT(ADDR(.payload) - LINMAP_OFFSET) ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        . = ALIGN(4096);
        _payload_end = .;
    } > RAM

    /* THE BOOT REGION TABLE and the child images it points at (sawos design 2
     * D-2). Both come from a stub the harness GENERATES per case: the table's
     * blob rows name the child sections' own symbols, so the LINKER fills in
     * the bases and nothing computes an address by hand.
     *
     * `.regions` gets the one new fixed symbol pair in the system — the kernel
     * reads it through the HAL's `sos_region_table_*` accessors, exactly as it
     * reads `.payload`'s bounds. An image built with no children has neither
     * input section, so start == end and the kernel sees ZERO REGIONS. That is
     * every case that existed before this unit, unchanged.
     *
     * 8-ALIGNED because the table's rows are pairs of 64-bit fields on both
     * profiles, which is what lets the kernel overlay a struct on them instead
     * of assembling bytes.
     *
     * NO PAGE ALIGNMENT, and the absence is the point on THIS profile
     * especially: neither section is ever granted to EL0. A child image is read
     * by the KERNEL at EL1 during the copy, so the grant window's page
     * granularity does not reach it, and the blob is dead bytes once the copy
     * is done. `.payload` above is page-aligned precisely because it IS
     * granted; these are not. */
    .regions : AT(ADDR(.regions) - LINMAP_OFFSET) ALIGN(8) {
        _region_table_start = .;
        KEEP(*(.regions))
        _region_table_end = .;
    } > RAM

    .childimg : AT(ADDR(.childimg) - LINMAP_OFFSET) ALIGN(16) {
        KEEP(*(.childimg))
    } > RAM

    .bss (NOLOAD) : AT(ADDR(.bss) - LINMAP_OFFSET) ALIGN(4096) {
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

    /* THE KERNEL MUST END BELOW ROOT'S REGION, and until sawos design 27
     * nothing checked it. The per-process translation-table pool put 48 KiB
     * into .bss in one edit (three 24 KiB sets where there was one), which is
     * the first change big enough to make the ceiling worth stating: overrun
     * 0x40200000 and the kernel's own .bss and root's image occupy the same
     * addresses, which corrupts root at load time with no diagnostic anywhere.
     *
     * `ROOT_LOAD_BASE` in hal/arm64/kernel/lib.saw is the same number; this is
     * the linker's copy of it, and a build that trips this assert should widen
     * the region map rather than shrink the pool silently.
     *
     * **IT CARRIES A SECOND JOB SINCE sawos design 29**, and it is worth knowing
     * before widening the ceiling: 0x40200000 is also the top of RAM's FIRST
     * 2 MiB block, and the linear map leaves exactly that one block executable
     * at EL1 (`KERNEL_IMAGE_BLOCK` in lib.saw) with every other block PXN. So an
     * image that outgrew this bound would also have run its own tail
     * non-executable. Widening it means widening the carve-out there too.
     *
     * Compared on the LOAD address: `_bss_end` is a high symbol now, and what
     * has to stay below root's base is where the bytes land. */
    ASSERT(_bss_end - LINMAP_OFFSET <= 0x40200000,
           "kernel .bss overruns ROOT_LOAD_BASE (0x40200000) -- see PROT_DOMAIN_SLOTS")
}
