/* SOS child process linker script: Xtensa, ESP32-S3 board.
 *
 * Root's script (`root.ld`) with the child's slot and RAM. Every process
 * shares one address space on this tier (spec §5.5), so two images cannot
 * both be linked at root's addresses. A child's image sits at the start of
 * the child slot in flash (`../kernel/esp32s3.ld`'s `CHILD_IMAGE_SLOT`, the
 * region table's first blob row), its code and read-only data are used in
 * place there, and its writable segment is linked at the base of the
 * destination region the region table publishes (the runner's
 * `child_region_base` for this board), which `process_create` copies it into:
 *
 *   flash slot + 0x60   code and literal pools, R+X, in place
 *   right behind it     read-only data, R, in place
 *   0x3FCC_8000         `.data`, `.bss` and the arena: DRAM, copied, R+W
 *   0x3FCD_C000         stack bottom: the kernel grants the stack separately
 *   0x3FCE_0000         region top, initial a1; the RAM pool starts here
 *
 * `root.ld` says how the addresses agree with where Blade puts the bytes and
 * what checks it. LENGTH stops at the stack bottom, so the linker cannot place
 * data where the stack will live. A second resident child would need a slot
 * and a script of its own; no case on this board has one.
 */

ENTRY(_start)

/* `sosrt`'s bump-allocator arena (hal/riscv32/user/root.ld describes the
 * knob). `.bss`-resident, so it costs nothing in the image but counts fully
 * against the region. */
PROVIDE(ARENA_SIZE = 48K);

/* Flash offset 0 in each bus window, the child slot and its length. */
FLASH_IBUS = 0x42000000;
FLASH_DBUS = 0x3C000000;
IMAGE_SLOT = 0x180000;
IMAGE_SLOT_LEN = 512K;

/* The image's header and its segment table, ahead of the code's bytes. */
SOSIMG_TABLE_LEN = 24 + 3 * 24;

/* The slot under each of its two names, and the DRAM. A section overrunning
 * one is a link error, never a silent overlap. */
MEMORY {
    SLOT_IBUS (rx) : ORIGIN = 0x42180000, LENGTH = 512K
    SLOT_DBUS (r)  : ORIGIN = 0x3C180000, LENGTH = 512K
    DATA (rwa)     : ORIGIN = 0x3FCC8000, LENGTH = 80K
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
       "child.ld: .text is empty, and the image is linked for three segments")
ASSERT(SIZEOF(.rodata) > 0,
       "child.ld: .rodata is empty, and the image is linked for three segments")
ASSERT(SIZEOF(.data) + SIZEOF(.bss) > 0,
       "child.ld: no .data or .bss, and the image is linked for three segments")
ASSERT(ORIGIN(SLOT_IBUS) == FLASH_IBUS + IMAGE_SLOT
       && ORIGIN(SLOT_DBUS) == FLASH_DBUS + IMAGE_SLOT
       && LENGTH(SLOT_IBUS) == IMAGE_SLOT_LEN
       && LENGTH(SLOT_DBUS) == IMAGE_SLOT_LEN,
       "child.ld: the slot's two memory regions are not IMAGE_SLOT")
ASSERT(ADDR(.text) == FLASH_IBUS + IMAGE_SLOT + SOSIMG_TABLE_LEN,
       "child.ld: the code does not start where the image puts its bytes")
ASSERT(ADDR(.rodata) == ADDR(.text) + SIZEOF(.text) - FLASH_IBUS + FLASH_DBUS,
       "child.ld: the read-only data does not start right behind the code")
ASSERT(SOSIMG_TABLE_LEN + SIZEOF(.text) + SIZEOF(.rodata) + SIZEOF(.data)
       <= IMAGE_SLOT_LEN,
       "child.ld: the image does not fit its flash slot")
