/* SOS kernel linker script: QEMU `virt`, arm64, EL1.
 *
 * RAM at 0x4000_0000 (where QEMU places an ELF given with `-kernel`).
 * `.text.boot` is placed first so the ELF entry is the reset target. Per-
 * function and per-data sections (`.text.*`, `.rodata.*`, `.data.*`, `.bss.*`,
 * emitted by sawc's freestanding profile and by
 * `clang -ffunction-sections -fdata-sections`) are caught by the wildcards
 * below and trimmed with `ld.lld --gc-sections`. A named section not matched
 * here needs its own entry.
 *
 * ---------------------------------------------------------------------------
 * Physical memory map
 * ---------------------------------------------------------------------------
 *   0x0800_0000  GIC distributor / CPU interface
 *   0x0900_0000  PL011 UART                       (the board's device window)
 *   0x4000_0000  kernel image: .text .rodata .data .payload, then .bss
 *                (the stack, the arena and the page-table pool live in .bss)
 *   0x4020_0000  root region base: 2 MiB in, clear of the kernel image, and a
 *                level-2 boundary so root's pages come out of the grant
 *                window whole; root's segments load here
 *   0x4023_C000  root stack bottom
 *   0x4024_0000  root region top (256 KiB region, 16 KiB stack)
 *
 * The root numbers are not set here: the HAL's `lib.saw` and the root linker
 * script own them, and the kernel validates every segment against the region
 * before placing it. They are recorded here because this file fixes the kernel
 * side of the map.
 *
 * ---------------------------------------------------------------------------
 * Protection layout
 * ---------------------------------------------------------------------------
 * EL0 is default-deny: the translation tables give EL0 nothing until a grant
 * says otherwise. Kernel code and data and the GIC are never granted. The
 * UART's page is the board's one device window: a process reaches it only once
 * it is granted, normally by mapping an `IoMemory` for it. The kernel's own
 * pages are EL1 read/write/execute.
 *
 * The grant window is the first 4 MiB of RAM at page granularity. It has to
 * cover the kernel image as well as the root region, because the harness's
 * payload cases grant a payload that executes in place inside the appended
 * `.payload` section.
 */

/* ---------------------------------------------------------------------------
 * The higher-half link
 * ---------------------------------------------------------------------------
 * The kernel links at high virtual addresses and loads at physical ones. Every
 * section below is placed at `physical + LINMAP_OFFSET` and given an explicit
 * load address of `ADDR(section) - LINMAP_OFFSET`, so:
 *
 *   - the ELF's program headers carry the physical addresses, and QEMU (which
 *     loads `-kernel` at each segment's `p_paddr`) puts the image there;
 *   - every symbol the kernel resolves at run time is a high address, which
 *     keeps the kernel reachable through TTBR1 and leaves TTBR0 purely the
 *     user's.
 *
 * Don't use a second MEMORY region for the load addresses: with two regions
 * the VMA and LMA cursors advance independently, and a section whose alignment
 * differs from its predecessor's silently breaks the fixed delta the scheme
 * rests on. Spelling each load address from the virtual one makes the delta an
 * identity.
 *
 * The entry point is physical. QEMU sets the reset PC from `e_entry` with the
 * MMU off, so a high entry would fault before the first instruction. `_start`
 * itself stays a high symbol; `_start_phys` below is its load address, and
 * that is what `ENTRY` names.
 *
 * The offset is also in `hal/arm64/kernel/lib.saw` (`LINMAP_OFFSET`), and
 * nothing at build time can compare the two, since a Saw static's value is not
 * a linker symbol. `linmap_offset_probe` compares them at boot, against the
 * address this script gave a static that file owns, before the linear map is
 * built. `LINMAP_PHYS_MASK` is exported for `boot.S`, which needs the
 * complement as a value it can load; the two ASSERTs below keep this file's own
 * pair consistent.
 */
LINMAP_OFFSET    = 0xFFFFFF8000000000;
LINMAP_PHYS_MASK = 0x0000007FFFFFFFFF;
ASSERT((LINMAP_OFFSET & LINMAP_PHYS_MASK) == 0,
       "the linear-map offset must not overlap the physical half")
ASSERT((LINMAP_OFFSET | LINMAP_PHYS_MASK) == 0xFFFFFFFFFFFFFFFF,
       "the linear-map offset and mask must partition the 64-bit address")

/* ---------------------------------------------------------------------------
 * The two `.bss` knobs
 * ---------------------------------------------------------------------------
 * Both regions are linker regions, so each is one number in the file that
 * fixes this side of the map.
 *
 * ARENA_SIZE: `sosrt`'s bump-allocator region, the storage behind
 *   `__saw_rt_alloc`. `PROVIDE`d rather than assigned because the user script
 *   lets a package override it (see `hal/arm64/user/user.ld`); nothing
 *   overrides the kernel's.
 *
 * KERNEL_STACK_SIZE: the boot stack `boot.S` loads into `sp`, and the stack
 *   the kernel runs on after a trap. A per-board number: this board has
 *   128 MiB, so 64 KiB costs nothing.
 *
 * CAPS is a number you may change; a leading underscore is an address the
 * program uses (`_stack_top` in `boot.S`, `_arena_start` / `_arena_end` through
 * `rt/common_c/support.c`, because Saw cannot name a linker symbol). Both
 * regions are inside `.bss`, so growing either one eats the same ceiling as
 * `PROT_DOMAIN_SLOTS` (see the ASSERT at the foot of this file).
 */
PROVIDE(ARENA_SIZE = 64K);
KERNEL_STACK_SIZE = 64K;

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

    /* The ELF entry: `_start`'s load address, where the reset PC has to land
     * because the MMU is off there. `boot.S` runs a handful of instructions at
     * this address and then jumps to its own high half. */
    _start_phys = _start - LINMAP_OFFSET;

    .rodata : AT(ADDR(.rodata) - LINMAP_OFFSET) {
        *(.rodata .rodata.*)
    } > RAM

    .data : AT(ADDR(.data) - LINMAP_OFFSET) {
        *(.data .data.*)
    } > RAM

    /* The appended root image: a blob the harness pulls in with `.incbin`,
     * bounded by linker symbols the kernel reads through the HAL's
     * `sos_payload_start`/`sos_payload_end` accessors (sink.c; Saw cannot name
     * a linker symbol). The harness's user-mode payloads ride the same slot as
     * raw code, which is why this section is executable. Empty (start == end)
     * in an image built without a payload.
     *
     * Page-aligned at both ends, which is load-bearing on this profile:
     * protection granularity here is the page, so a payload sharing a page with
     * kernel code would grant EL0 that code. Rounding the end up keeps the
     * grant from spilling into `.bss`. */
    .payload : AT(ADDR(.payload) - LINMAP_OFFSET) ALIGN(4096) {
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
     * 8-aligned because the table's rows are pairs of 64-bit fields, which
     * lets the kernel overlay a struct on them instead of assembling bytes.
     *
     * No page alignment: neither section is ever granted to EL0. A child image
     * is read by the kernel at EL1 during the copy, so the grant window's page
     * granularity does not reach it, and the blob is dead bytes once the copy
     * is done. `.payload` above is page-aligned because it is granted. */
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

        /* The arena and the boot stack, both inside `.bss` so `boot.S`'s zero
         * loop (`_bss_start`..`_bss_end`) covers them. The stack keeps the
         * 16-byte alignment AArch64's `sp` requires. */
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
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
        *(.ARM.attributes)
        *(.ARM.exidx*)
    }

    /* The kernel must end below root's region. Overrunning 0x40200000 puts the
     * kernel's own .bss and root's image at the same addresses, which corrupts
     * root at load time with no diagnostic anywhere.
     *
     * `ROOT_LOAD_BASE` in hal/arm64/kernel/lib.saw is the same number; this is
     * the linker's copy of it. The message names `PROT_DOMAIN_SLOTS` because
     * the translation-table pool is the `.bss` reservation that grows with it.
     * A build that trips this assert should widen the region map rather than
     * silently shrink the pool.
     *
     * The bound has a second job: 0x40200000 is also the top of RAM's first
     * 2 MiB block, and the linear map leaves exactly that one block executable
     * at EL1 (`KERNEL_IMAGE_BLOCK` in lib.saw) with every other block PXN. An
     * image that outgrew this bound would also have its tail non-executable.
     * Widening it means widening the carve-out there too.
     *
     * Compared on the load address: `_bss_end` is a high symbol, and what has
     * to stay below root's base is where the bytes land. */
    ASSERT(_bss_end - LINMAP_OFFSET <= 0x40200000,
           "kernel .bss overruns ROOT_LOAD_BASE (0x40200000) -- see PROT_DOMAIN_SLOTS")
}
