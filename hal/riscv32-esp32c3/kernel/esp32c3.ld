/* SOS kernel linker script: ESP32-C3 board profile.
 *
 * Counterpart of hal/riscv32/kernel/virt.ld: the section list, the wildcards
 * and the .payload/.regions/.childimg contract are the same; the MEMORY map
 * and the XIP split differ. Keep the two in step.
 *
 * ---------------------------------------------------------------------------
 * XIP
 * ---------------------------------------------------------------------------
 * The kernel's share of SRAM is the `SRAM` region below; root, a child and the
 * pool divide the rest. The kernel's code and constants do not fit beside its
 * .data and .bss in that share, so .text and .rodata execute in place from the
 * flash instruction window. Only .data is copied to SRAM (by boot.S) and .bss
 * zeroed there.
 *
 * .payload / .regions / .childimg stay in flash too. They are read-only blobs
 * the kernel copies out of (the sosimg loader copies root's segments into the
 * root region), so nothing is granted to a process where it sits. A payload
 * granted in place would not work here, since it would be a read-only flash
 * grant.
 *
 * ---------------------------------------------------------------------------
 * Physical memory map (from Espressif QEMU's `info mtree`; see ../ABI.md §1)
 * ---------------------------------------------------------------------------
 *   0x3C00_0000  flash DBUS window, 8 MiB          (ROM maps flash offset 0)
 *   0x3FC8_0000  SRAM via the DRAM window          (alias of IRAM + 0x4000)
 *   0x4000_0000  mask ROM, 384 KiB
 *   0x4037_C000  SRAM, 400 KiB                     the only RAM
 *   0x4200_0000  flash IBUS window, 8 MiB          (alias of the DBUS window)
 *   0x6000_0000  UART0        0x6002_3000  SYSTIMER
 *   0x6001_F000  TIMG0        0x600C_2000  INTERRUPT MATRIX
 *
 * Flash image layout, per the ROM's direct-boot protocol (../ABI.md §2):
 *   0x4200_0000  0xAEDB041D                (direct-boot magic word 1)
 *   0x4200_0004  0xAEDB041D                (direct-boot magic word 2)
 *   0x4200_0008  _start                    the ROM `jalr`s here
 *   ...          .text .rodata .payload .regions .childimg
 *   (then)       .data's load image, copied to SRAM by boot.S
 *
 * SRAM budget:
 *   0x4037_C000  kernel .data + .bss (the stack is inside .bss)
 *   0x403A_6000  root region base (image + stack)
 *   0x403C_5000  child region base (image + stack)
 *   0x403D_C000  RAM pool
 *   0x403E_0000  end of SRAM
 *
 * The kernel's .data + .bss must fit the `SRAM` region below. An overshoot is a
 * link error ("will not fit in region"), never a silent overlap. The
 * `ARENA_SIZE` and `KERNEL_STACK_SIZE` knobs below are the levers when it does
 * not fit.
 *
 * The root and child numbers are not set here: root.ld, child.ld and the HAL's
 * `lib.saw` (`ROOT_LOAD_BASE`, `ROOT_REGION_TOP`) own them, the harness's
 * region table publishes the child's destination, and the kernel validates
 * every segment against its region before placing it. The pool is a reservation
 * in this budget only; this board's region table has no pool row.
 *
 * ---------------------------------------------------------------------------
 * PMP region layout (same as the virt profile)
 * ---------------------------------------------------------------------------
 * U-mode is default-deny and the C3 implements all sixteen PMP entries
 * (../ABI.md §4), so the eight TOR-pair regions the virt profile budgets
 * transfer unchanged.
 *
 * SRAM is reachable at two addresses (the IRAM window used here and the DRAM
 * alias 0x3FC8_0000). Default-deny covers the alias: only granted addresses
 * are reachable. But a HAL must never grant both windows for one region, or
 * one unmap would revoke only one of the two doors.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

/* ---------------------------------------------------------------------------
 * The two `.bss` knobs
 * ---------------------------------------------------------------------------
 * `ARENA_SIZE` is the size of `sosrt`'s bump-allocator arena (virt.ld
 * describes it in full). It is 16K here, smaller than virt's, so the kernel's
 * .data + .bss fit the 168K `SRAM` region; that is safe because the kernel
 * allocates nothing on its steady path and the arena backs only the compiler's
 * panic path. `KERNEL_STACK_SIZE` is the boot stack, left at the virt board's
 * 64K.
 *
 * `PROT_DOMAIN_SLOTS` also costs this board `.bss`: it sizes the kernel's stats
 * region. It is not a board knob, though. It is `rv32core`'s, shared with the
 * virt and flat profiles, so changing it changes every riscv32 profile.
 */
PROVIDE(ARENA_SIZE = 16K);
KERNEL_STACK_SIZE = 64K;

MEMORY {
    /* The ROM maps flash offset 0 here before jumping in. 4 MiB is what it
     * maps (0x40 pages of 64 KiB). */
    FLASH (rx)  : ORIGIN = 0x42000000, LENGTH = 4M

    /* Kernel .data + .bss only. The rest of SRAM is the root, child and pool
     * regions in the budget above. */
    SRAM  (rwa) : ORIGIN = 0x4037C000, LENGTH = 168K
}

SECTIONS {
    /* The direct-boot magic. Two words of 0xAEDB041D at flash offsets 0 and 4
     * are the whole boot protocol on this part: the mask ROM checks them, maps
     * flash at 0x4200_0000 (IBUS) and 0x3C00_0000 (DBUS), and then `jalr`s to
     * 0x4200_0008, so .text.boot must be the very next thing. */
    .magic 0x42000000 : {
        LONG(0xAEDB041D)
        LONG(0xAEDB041D)
    } > FLASH

    /* Lands at 0x4200_0008 — the ROM's call target. */
    .text : {
        KEEP(*(.text.boot))
        *(.text .text.*)
    } > FLASH

    .rodata : {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
    } > FLASH

    /* The appended root image: a blob the harness pulls in with `.incbin`,
     * bounded by linker symbols the kernel reads through the kernel HAL's
     * `sos_payload_start`/`sos_payload_end` accessors (sink.c; Saw cannot name
     * a linker symbol). Empty (start == end) in an image built without a
     * payload.
     *
     * Page-aligned at both ends, for the virt profile's two reasons: a blob is
     * page-granular wherever it lives, and QEMU's softmmu takes a slow path for
     * a page a PMP region only partly covers. It sits in flash because the
     * loader copies root's segments into the root region, so the blob is never
     * itself granted to a process. */
    .payload : ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        . = ALIGN(4096);
        _payload_end = .;
    } > FLASH

    /* The boot region table and the child images it points at, from a stub
     * the harness generates per case. 8-aligned because the rows are pairs of
     * 64-bit fields. No page alignment is needed: neither section is ever
     * granted. The kernel reads them in M-mode during the copy. */
    .regions : ALIGN(8) {
        _region_table_start = .;
        KEEP(*(.regions))
        _region_table_end = .;
    } > FLASH

    .childimg : ALIGN(16) {
        KEEP(*(.childimg))
    } > FLASH

    /* The one section that moves. VMA in SRAM, LMA in flash: boot.S copies
     * [_data_lma, _data_lma + (_data_end - _data_start)) to _data_start before
     * anything reads a mutable global. */
    .data : ALIGN(4) {
        _data_start = .;
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
        _data_end = .;
    } > SRAM AT> FLASH
    _data_lma = LOADADDR(.data);

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
    } > SRAM

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
