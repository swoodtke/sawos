/* SOS kernel linker script: QEMU `virt`, riscv32.
 *
 * RAM at 0x8000_0000 (where QEMU jumps with `-bios none -kernel`). `.text.boot`
 * is placed first so the ELF entry (`_start`) is the reset target. Per-function
 * and per-data sections (`.text.*`, `.rodata.*`, `.data.*`, `.bss.*`, emitted
 * by sawc's freestanding profile and by
 * `clang -ffunction-sections -fdata-sections`) are caught by the wildcards
 * below and trimmed with `ld.lld --gc-sections`. A named section not matched
 * here needs its own output-section entry.
 *
 * ---------------------------------------------------------------------------
 * Physical memory map
 * ---------------------------------------------------------------------------
 *   0x0010_0000  sifive_test finisher       (never granted; writable on flat)
 *   0x1000_0000  NS16550A UART              (the board's device window)
 *   0x8000_0000  kernel image: .text .rodata .data .payload, then .bss
 *                (the stack and the arena live in .bss)
 *   0x8020_0000  root region base: 2 MiB in, clear of the kernel image;
 *                root's segments load here
 *   0x8023_C000  root stack bottom
 *   0x8024_0000  root region top (256 KiB region, 16 KiB stack)
 *
 * The root numbers are not set here: root.ld and the HAL's `lib.saw`
 * (`ROOT_LOAD_BASE`, `ROOT_REGION_TOP`) own them, and the kernel validates
 * every segment against the region before placing it. They are recorded here
 * because this file fixes the kernel side of the map.
 *
 * ---------------------------------------------------------------------------
 * PMP region layout
 * ---------------------------------------------------------------------------
 * U-mode is default-deny: RISC-V fails any U-mode access that matches no PMP
 * entry. Kernel code and data and the finisher are never granted. The UART's
 * page is the board's one device window: a process reaches it only once it is
 * granted, normally by mapping an `IoMemory` for it. M-mode is unconstrained
 * (SOS sets no L bits).
 *
 * That is the isolated profile. The flat profile links this same script, but
 * its HAL opens one U-mode range over the whole address space at boot, so none
 * of the protection described here applies there.
 *
 * Regions are TOR pairs: entry 2*i is the lower bound (A=OFF, a bound only),
 * entry 2*i+1 is the upper bound (A=TOR) and carries R/W/X. Eight regions use
 * all sixteen of QEMU's entries, which is every entry the part implements:
 *
 *   region 0..N-1  one per sosimg segment, permissions from the segment flags
 *   region N       the root stack, RW
 *   region N+1..   rows installed at run time by MemoryOp.Map / IoMemoryOp.Map
 *
 * A device window costs one region rather than a pair, because it is staged as
 * a naturally-aligned power-of-two (NAPOT) entry: the pair's high entry carries
 * the config and the low one stays disabled.
 *
 * A sosimg with more segments than the budget allows is rejected as a malformed
 * image rather than being silently under-protected, and a map() that would need
 * a ninth region is refused with NoResource rather than dropped.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

/* ---------------------------------------------------------------------------
 * The two `.bss` knobs
 * ---------------------------------------------------------------------------
 * Both regions are linker regions, so each is one number in the file that
 * fixes this side of the map.
 *
 * ARENA_SIZE: `sosrt`'s bump-allocator region, the storage behind
 *   `__saw_rt_alloc`. `PROVIDE`d rather than assigned because the user scripts
 *   let a package override it (see `hal/riscv32/user/root.ld`); nothing
 *   overrides the kernel's.
 *
 * KERNEL_STACK_SIZE: the boot stack `boot.S` sets `sp` to and `trap.S` reloads
 *   on every trap. A per-board number: this board has 128 MiB, so 64 KiB costs
 *   nothing.
 *
 * CAPS is a number you may change; a leading underscore is an address the
 * program uses (`_stack_top` in the two assembly files, `_arena_start` /
 * `_arena_end` through `rt/common_c/support.c`, because Saw cannot name a
 * linker symbol).
 */
PROVIDE(ARENA_SIZE = 64K);
KERNEL_STACK_SIZE = 64K;

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

    /* The appended root image: a blob the harness pulls in with `.incbin`,
     * bounded by linker symbols the kernel reads through the HAL's
     * `sos_payload_start`/`sos_payload_end` accessors (sink.c; Saw cannot name
     * a linker symbol). The harness's U-mode payloads ride the same slot as raw
     * code, which is why this section is executable. Empty (start == end) in an
     * image built without a payload.
     *
     * Page-aligned at both ends, each for its own reason.
     *
     * The start: a payload that straddles a page boundary is awkward. With the
     * C extension enabled a 4-byte instruction can begin at a 2-mod-4 address
     * and span two pages, which QEMU 11.0.3 asserts on inside its own
     * translator.
     *
     * The end: speed under emulation. PMP grants at four-byte granularity, so a
     * grant ending mid-page is legal hardware, but QEMU's softmmu caches
     * translations a page at a time and takes its slow path for every access to
     * a page a PMP region only partly covers, which makes every user-mode case
     * far slower. Keep the end aligned.
     *
     * The pages between these two symbols belong to `.payload` alone: `.data`
     * ends before the ALIGN above, and the sections that follow (`.regions`,
     * `.childimg`, then `.bss`) start at or after the ALIGN here, so a grant of
     * the whole span reaches nothing else. Rounding the end is therefore safe
     * as well as fast, and removes no check. */
    .payload : ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        . = ALIGN(4096);
        _payload_end = .;
    } > RAM

    /* The boot region table and the child images it points at. Both come from
     * a stub the harness generates per case: the table's blob rows name the
     * child sections' own symbols, so the linker fills in the bases.
     *
     * The kernel reads the table through the HAL's `sos_region_table_*`
     * accessors, as it reads `.payload`'s bounds. An image built with no
     * children has neither input section, so start == end and the kernel sees
     * zero regions.
     *
     * 8-aligned because the table's rows are pairs of 64-bit fields on both
     * profiles, which lets the kernel overlay a struct on them instead of
     * assembling bytes.
     *
     * No page alignment. The table is never granted to a process. A child
     * image is read by the kernel during the copy (at M-mode here), so no
     * grant window constrains where it lands; a segment of it linked where its
     * own bytes sit is used in place instead, granted to the child at four-byte
     * grain (spec 5.6), and the blob holds that child's code for its life. */
    .regions : ALIGN(8) {
        _region_table_start = .;
        KEEP(*(.regions))
        _region_table_end = .;
    } > RAM

    .childimg : ALIGN(16) {
        KEEP(*(.childimg))
    } > RAM

    .bss (NOLOAD) : {
        _bss_start = .;
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)

        /* The arena and the boot stack, both inside `.bss` so `boot.S`'s zero
         * loop (`_bss_start`..`_bss_end`) covers them. */
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;

        . = ALIGN(16);
        _stack_bottom = .;
        . = . + KERNEL_STACK_SIZE;
        _stack_top = .;

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
