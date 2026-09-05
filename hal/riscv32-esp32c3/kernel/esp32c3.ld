/* SOS kernel linker script — ESP32-C3 board profile (design 20).
 *
 * ORIGIN: sibling copy of hal/riscv32/kernel/virt.ld (the SIBLING-COPY rule,
 * hal/riscv32-esp32c3/README.md). The section list, the wildcards, the
 * .payload/.regions/.childimg contract and their comments are that file's;
 * what changed is the MEMORY map and the XIP split. Keep the pair in step
 * until the named shared-core dedup pass merges them.
 *
 * ---------------------------------------------------------------------------
 * WHY XIP, AND WHAT IT COSTS (design 20, blocker A, user-ruled Sep 2)
 * ---------------------------------------------------------------------------
 * The kernel's loadable image is ~445 KiB and the ESP32-C3 has 400 KiB of
 * SRAM TOTAL, so the brief's original copy-to-SRAM default cannot hold it —
 * .text alone is 404 KiB. The ruling: .text and .rodata stay IN PLACE in the
 * flash instruction window, and only .data is copied and .bss zeroed in SRAM
 * (~163 KiB, which fits with room for root, a child and the pool). Design 19's
 * Sep-2 addendum already named XIP the ESP32 family's execution model; this is
 * that, made real.
 *
 * .payload / .regions / .childimg stay in FLASH too. They are read-only blobs
 * the KERNEL copies out of (the sosimg loader copies root's segments into the
 * root region), so nothing is granted to a process where it sits and leaving
 * them in flash buys back another ~39 KiB of SRAM. A payload GRANTED IN PLACE
 * would not survive this — it would be a read-only flash grant — but no case
 * does that here.
 *
 * ---------------------------------------------------------------------------
 * PHYSICAL MEMORY MAP — derived from Espressif QEMU's `info mtree` and probed
 * on the running machine, not from a datasheet (see ../ABI.md §1)
 * ---------------------------------------------------------------------------
 *   0x3C00_0000  flash DBUS window, 8 MiB          (ROM maps flash offset 0)
 *   0x3FC8_0000  SRAM via the DRAM window          (alias of IRAM + 0x4000)
 *   0x4000_0000  mask ROM, 384 KiB
 *   0x4037_C000  SRAM, 400 KiB                     THE ONLY RAM
 *   0x4200_0000  flash IBUS window, 8 MiB          (alias of the DBUS window)
 *   0x6000_0000  UART0        0x6002_3000  SYSTIMER
 *   0x6001_F000  TIMG0        0x600C_2000  INTERRUPT MATRIX
 *
 * FLASH IMAGE LAYOUT — the ROM's direct-boot protocol (../ABI.md §2):
 *   0x4200_0000  0xAEDB041D                (direct-boot magic word 1)
 *   0x4200_0004  0xAEDB041D                (direct-boot magic word 2)
 *   0x4200_0008  _start                    THE ROM `jalr`s HERE
 *   ...          .text .rodata .payload .regions .childimg
 *   (then)       .data's load image, copied to SRAM by boot.S
 *
 * SRAM BUDGET — the whole 400 KiB, accounted:
 *   0x4037_C000  kernel .data + .bss (64 KiB stack inside .bss)  168 KiB
 *   0x403A_6000  ROOT REGION BASE      (108K image + 16K stack)   124 KiB
 *   0x403C_5000  CHILD REGION BASE     ( 76K image + 16K stack)    92 KiB
 *   0x403D_C000  RAM POOL                                          16 KiB
 *   0x403E_0000  end of SRAM
 *
 * Every one of those four numbers is MEASURED, not chosen for tidiness. At
 * design 20 the kernel needed 167,360 B of .data+.bss and got 172,032; root's
 * largest image (c3-isolation) needed 99,392 B and got 110,592; the child
 * needed 67,600 and got 77,824. The slack was thousands of bytes rather than
 * tens of thousands, which is what 400 KiB of SRAM looks like once this kernel
 * is in it — and an overshoot is a LOUD ld.lld "will not fit in region" error,
 * never a silent overlap.
 *
 * **AND IT IS OVERSHOOTING NOW, LOUDLY** (re-measured sawos design 38, Sep 5).
 * The kernel wants 182,424 B of .data+.bss against the 172,032 this region
 * grants, so `make sos-smoke-esp32c3` fails at the LINK with that error and has
 * done since design 37 (`9026bf7`) — bisected: 3/3 at `a64d359`, 0/3 at every
 * commit after it. Design 37's stats region is 12,288 B of `.bss` on this board
 * (it is sized by `PROT_DOMAIN_SLOTS`, which riscv32 publishes as 256) and the
 * board had under 2 KiB of slack to pay it with. THIS FILE IS NOT WHERE THAT IS
 * FIXED and design 38 deliberately did not fix it — retuning a board is that
 * board's own unit — but the two knobs below are now the levers, and the fit
 * was PROBED rather than assumed: `ARENA_SIZE = 16K` alone frees 48 KiB and
 * takes the smoke back to 3/3 with 38,760 B of slack. A kernel that allocates
 * nothing on its steady path (every kernel source builds under
 * `--no-hidden-alloc`) is the argument for that being the first lever tried.
 *
 * The root/child/pool numbers are NOT set here — root.ld, child.ld and the
 * harness's region table own them, and the kernel validates every segment
 * against its region before placing it. They are recorded here because this is
 * the file that fixes the kernel side of the map.
 *
 * ---------------------------------------------------------------------------
 * PMP REGION LAYOUT — unchanged from the virt profile
 * ---------------------------------------------------------------------------
 * U-mode is default-deny and the C3 implements all SIXTEEN PMP entries
 * (probed — ../ABI.md §4), which is design 19 tier 2's ruled floor met exactly,
 * so the eight TOR-pair regions the virt profile budgets transfer unchanged.
 *
 * ONE BOARD NOTE the virt profile has no equivalent for: SRAM is reachable at
 * TWO addresses (the IRAM window used here and the DRAM alias 0x3FC8_0000).
 * Default-deny handles it — only granted addresses are reachable, so the alias
 * of a kernel page is denied like any other ungranted address — but a HAL must
 * never grant BOTH windows for one region, or one unmap would revoke one of
 * two doors.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

/* ---------------------------------------------------------------------------
 * THE TWO `.bss` KNOBS (sawos design 38, M5 arena unit)
 * ---------------------------------------------------------------------------
 * Sibling-copied from `hal/riscv32/kernel/virt.ld`, which carries the full
 * argument. What is worth saying HERE is that this is the board the knobs were
 * built for: 400 KiB of SRAM total, of which 168 KiB is this section's budget
 * and 167,360 bytes are already spent (the header above).
 *
 * THE ARENA IS RETUNED TO 16K AND THE STACK LEFT AT THE VIRT BOARD'S (the
 * design-38 integration, Sep 5 — the lead applied design 38's own probe after
 * the unit merged, because the smoke had been red since design 37 and the
 * seventh pin bump's -Oz re-measure needs a board that links). 16K is the
 * probed value: it alone frees 48 KiB and takes the smoke back to 3/3 with
 * 38,760 B of slack, and a kernel that allocates nothing on its steady path
 * (every kernel source builds under `--no-hidden-alloc`; the arena backs only
 * the compiler's panic path) is the argument for touching this lever and not
 * the stack's. Whoever takes design 20's port still holds the other two
 * levers: `KERNEL_STACK_SIZE` below, and `PROT_DOMAIN_SLOTS`, which sizes
 * both the domain pool and design 37's stats region on this board (that
 * finding is filed there).
 */
PROVIDE(ARENA_SIZE = 16K);
KERNEL_STACK_SIZE = 64K;

MEMORY {
    /* The ROM maps flash offset 0 here before jumping in. 4 MiB is what it
     * maps (0x40 pages of 64 KiB); the image uses well under half of it. */
    FLASH (rx)  : ORIGIN = 0x42000000, LENGTH = 4M

    /* Kernel .data + .bss ONLY. The rest of SRAM is the root, child and pool
     * regions in the budget above. */
    SRAM  (rwa) : ORIGIN = 0x4037C000, LENGTH = 168K
}

SECTIONS {
    /* THE DIRECT-BOOT MAGIC. Two words of 0xAEDB041D at flash offsets 0 and 4
     * are the ENTIRE boot protocol on this part: the mask ROM checks them,
     * maps flash at 0x4200_0000 (IBUS) and 0x3C00_0000 (DBUS), and then `jalr`s
     * to 0x4200_0008 — which is why .text.boot must be the very next thing.
     * Verified by disassembling the bundled ROM and then by booting. */
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

    /* The appended root image (design 140 unit B): a blob the harness pulls in
     * with `.incbin`, bounded by linker symbols the kernel reads through the
     * kernel HAL's `sos_payload_start`/`sos_payload_end` accessors (sink.c —
     * Saw cannot name a linker symbol). Empty (start == end) in an image built
     * without a payload.
     *
     * PAGE-aligned at both ends, keeping the virt profile's two reasons (a
     * blob is page-granular wherever it lives, and QEMU's softmmu takes a slow
     * path for a page a PMP region only partly covers). In FLASH here: the
     * loader COPIES root's segments into the root region, so this blob is
     * never itself granted to a process. */
    .payload : ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        . = ALIGN(4096);
        _payload_end = .;
    } > FLASH

    /* THE BOOT REGION TABLE and the child images it points at (sawos design 2
     * D-2), from a stub the harness generates per case. 8-ALIGNED because the
     * rows are pairs of 64-bit fields. No page alignment, and the absence is
     * the point: neither section is ever GRANTED — the kernel reads them at
     * M-mode during the copy and the bytes are dead afterwards. */
    .regions : ALIGN(8) {
        _region_table_start = .;
        KEEP(*(.regions))
        _region_table_end = .;
    } > FLASH

    .childimg : ALIGN(16) {
        KEEP(*(.childimg))
    } > FLASH

    /* THE ONE SECTION THAT MOVES. VMA in SRAM, LMA in flash: boot.S copies
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
         * loop (which runs `_bss_start`..`_bss_end`) covers them exactly as it
         * covered the `static` and the `.skip` they replace. */
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
