/* SOS root server linker script: Xtensa, ESP32-S3 board.
 *
 * Root's code and read-only data are used in place in flash, and only its
 * writable data, arena and stack take RAM (spec §5.6). The kernel's script
 * (`../kernel/esp32s3.ld`) puts root's image at the start of a fixed flash
 * slot, so this script links each segment at the address of the flash bytes
 * the image will put it in, and the loader finds it there:
 *
 *   flash slot + 0x00   the sosimg header and segment table, 0x60 bytes
 *   flash slot + 0x60   code and literal pools: linked at the slot's
 *                       instruction-window address plus 0x60, R+X
 *   right behind it     read-only data: linked at the data-window address of
 *                       its own flash bytes, R
 *   0x3FCB_0000         `.data`, `.bss` and the arena: DRAM, copied, R+W
 *   0x3FCC_4000         stack bottom: the kernel grants the stack separately
 *   0x3FCC_8000         ROOT_REGION_TOP, initial a1
 *
 * Where a segment's bytes sit is Blade's packing: a 24-byte header, a 24-byte
 * record per segment in program-header order, then each segment's bytes on a
 * 4-byte boundary (`imgformat`, `blade/src/sosimg.saw`). With the three
 * segments `PHDRS` declares, the code starts at 0x60 and the read-only data
 * right behind the code, which ends on 16 bytes. Three things hold the build
 * to it: the ASSERTs below, the runner, which reads each image back out of
 * the flash image and fails the build when a segment is not linked where its
 * bytes sit, and the loader, which on this board refuses a segment that is
 * not in place unless it is linked in DRAM, and refuses an executable one
 * there too.
 *
 * `PHDRS` is declared so the linker loads no ELF header page below the code,
 * which would be a fourth segment and land outside the slot.
 *
 * The slot and the DRAM region are decisions recorded twice: `IMAGE_SLOT`
 * here and `ROOT_IMAGE_SLOT` in the kernel's script, and the RAM below and
 * the root-region constants in `../kernel/lib.saw`. Nothing translates, so a
 * script that disagrees produces an image the build or the loader refuses.
 * LENGTH stops at the stack bottom, so the linker cannot place data where
 * the stack will live.
 */

ENTRY(_start)

/* `sosrt`'s bump-allocator arena (hal/riscv32/user/root.ld describes the
 * knob). `.bss`-resident, so it costs nothing in the image but counts fully
 * against the region. */
PROVIDE(ARENA_SIZE = 48K);

/* Flash offset 0 in each bus window, root's slot and its length. */
FLASH_IBUS = 0x42000000;
FLASH_DBUS = 0x3C000000;
IMAGE_SLOT = 0x100000;
IMAGE_SLOT_LEN = 512K;

/* The image's header and its segment table, ahead of the code's bytes. */
SOSIMG_TABLE_LEN = 24 + 3 * 24;

/* The slot under each of its two names, and the DRAM. A section overrunning
 * one is a link error, never a silent overlap. */
MEMORY {
    SLOT_IBUS (rx) : ORIGIN = 0x42100000, LENGTH = 512K
    SLOT_DBUS (r)  : ORIGIN = 0x3C100000, LENGTH = 512K
    DATA (rwa)     : ORIGIN = 0x3FCB0000, LENGTH = 80K
}

PHDRS {
    code PT_LOAD FLAGS(5);      /* PF_R | PF_X */
    rodata PT_LOAD FLAGS(4);    /* PF_R */
    data PT_LOAD FLAGS(6);      /* PF_R | PF_W */
}

SECTIONS {
    /* Code, each function beside the literals it loads: `l32r` reaches only
     * backwards, and listing every pool first would put the last functions
     * out of reach once the code passes its 256 KiB range. Ends on 16 bytes,
     * which the read-only data behind it needs. */
    .text (FLASH_IBUS + IMAGE_SLOT + SOSIMG_TABLE_LEN) : {
        *(.literal .literal.* .text .text.*)
        . = ALIGN(16);
        _text_end = .;
    } > SLOT_IBUS :code

    /* Read-only data: a segment of its own, readable and neither writable nor
     * executable, so a string literal is no part of executable memory. Linked
     * at the data-window name of the flash right behind the code, where Blade
     * packs it. */
    .rodata (ADDR(.text) + SIZEOF(.text) - FLASH_IBUS + FLASH_DBUS) : {
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > SLOT_DBUS :rodata

    .data : ALIGN(16) {
        *(.data .data.*)
        . = ALIGN(4);
    } > DATA :data

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > DATA :data

    /DISCARD/ : {
        *(.comment)
        *(.xtensa.info)
        *(.xt.prop .xt.prop.*)
        *(.xt.lit .xt.lit.*)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}

/* An empty section's program header carries nothing, Blade emits no record
 * for it, and every offset behind it moves by a record. */
ASSERT(SIZEOF(.text) > 0,
       "root.ld: .text is empty, and the image is linked for three segments")
ASSERT(SIZEOF(.rodata) > 0,
       "root.ld: .rodata is empty, and the image is linked for three segments")
ASSERT(SIZEOF(.data) + SIZEOF(.bss) > 0,
       "root.ld: no .data or .bss, and the image is linked for three segments")
ASSERT(ORIGIN(SLOT_IBUS) == FLASH_IBUS + IMAGE_SLOT
       && ORIGIN(SLOT_DBUS) == FLASH_DBUS + IMAGE_SLOT
       && LENGTH(SLOT_IBUS) == IMAGE_SLOT_LEN
       && LENGTH(SLOT_DBUS) == IMAGE_SLOT_LEN,
       "root.ld: the slot's two memory regions are not IMAGE_SLOT")
ASSERT(ADDR(.text) == FLASH_IBUS + IMAGE_SLOT + SOSIMG_TABLE_LEN,
       "root.ld: the code does not start where the image puts its bytes")
ASSERT(ADDR(.rodata) == ADDR(.text) + SIZEOF(.text) - FLASH_IBUS + FLASH_DBUS,
       "root.ld: the read-only data does not start right behind the code")
ASSERT(SOSIMG_TABLE_LEN + SIZEOF(.text) + SIZEOF(.rodata) + SIZEOF(.data)
       <= IMAGE_SLOT_LEN,
       "root.ld: the image does not fit its flash slot")
