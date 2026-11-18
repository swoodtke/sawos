/* SOS process linker script: Xtensa, ESP32-S3 board.
 *
 * One script for every process on this board, root included. It names no
 * address: the image builder links each process with a script of its own that
 * assigns five symbols and then INCLUDEs the script the package's manifest
 * names, this one or a package's own that INCLUDEs it (`sosimage package`,
 * `tools/sosimage/src/layout.saw`):
 *
 *   SOS_SLOT_BASE    the flash offset of the process's slot, where the
 *                    kernel's script puts its image (`image_slots.ld`)
 *   SOS_SLOT_LEN     the slot's length
 *   SOS_REGION_BASE  where its writable segment loads, in DRAM
 *   SOS_REGION_LEN   the region's length, its stack included
 *   SOS_STACK_LEN    the stack at the region's top, which the kernel grants
 *                    separately; the initial a1 is the region's top
 *
 * A process's code and read-only data are used in place in flash, and only
 * its writable data, arena and stack take RAM (spec §5.6). So this script
 * links each segment at the address of the flash bytes the image will put it
 * in, and the loader finds it there:
 *
 *   slot + 0x00          the sosimg header and segment table, 0x60 bytes
 *   slot + 0x60          code and literal pools: linked at the slot's
 *                        instruction-window address plus 0x60, R+X
 *   right behind it      read-only data: linked at the data-window address of
 *                        its own flash bytes, R
 *   SOS_REGION_BASE      `.data`, `.bss` and the arena: DRAM, copied, R+W
 *   region top - stack   stack bottom: the kernel grants the stack separately
 *   region top           initial a1
 *
 * Where a segment's bytes sit is Blade's packing: a 24-byte header, a 24-byte
 * record per segment in program-header order, then each segment's bytes on a
 * 4-byte boundary (`imgformat`, `blade/src/sosimg.saw`). With the three
 * segments `PHDRS` declares, the code starts at 0x60 and the read-only data
 * right behind the code, which ends on 16 bytes. Three things hold the build
 * to it: the ASSERTs below, the builder, which reads each image back out of
 * the flash image and fails the build when a segment is not linked where its
 * bytes sit, and the loader, which on this board refuses a segment that is
 * not in place unless it is linked in DRAM, and refuses an executable one
 * there too.
 *
 * `PHDRS` is declared so the linker loads no ELF header page below the code,
 * which would be a fourth segment and land outside the slot.
 *
 * Nothing translates, so every process is linked for its own slot and its own
 * region; the builder assigns both, and the same numbers reach the kernel's
 * script and the region table, so a disagreement is an image the build or
 * the loader refuses. LENGTH stops at the stack bottom, so the linker cannot
 * place data where the stack will live.
 */

ENTRY(_start)

/* `sosrt`'s heap arena (hal/riscv32/user/user.ld describes the
 * knob). `.bss`-resident, so it costs nothing in the image but counts fully
 * against the region. */
PROVIDE(ARENA_SIZE = 48K);

/* Flash offset 0 in each bus window. */
FLASH_IBUS = 0x42000000;
FLASH_DBUS = 0x3C000000;

/* The image's header and its segment table, ahead of the code's bytes. */
SOSIMG_TABLE_LEN = 24 + 3 * 24;

/* The slot under each of its two names, and the DRAM. A section overrunning
 * one is a link error, never a silent overlap. */
MEMORY {
    SLOT_IBUS (rx) : ORIGIN = FLASH_IBUS + SOS_SLOT_BASE, LENGTH = SOS_SLOT_LEN
    SLOT_DBUS (r)  : ORIGIN = FLASH_DBUS + SOS_SLOT_BASE, LENGTH = SOS_SLOT_LEN
    DATA (rwa)     : ORIGIN = SOS_REGION_BASE, LENGTH = SOS_REGION_LEN - SOS_STACK_LEN
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
    .text (FLASH_IBUS + SOS_SLOT_BASE + SOSIMG_TABLE_LEN) : {
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
       "user.ld: .text is empty, and the image is linked for three segments")
ASSERT(SIZEOF(.rodata) > 0,
       "user.ld: .rodata is empty, and the image is linked for three segments")
ASSERT(SIZEOF(.data) + SIZEOF(.bss) > 0,
       "user.ld: no .data or .bss, and the image is linked for three segments")
ASSERT(ADDR(.text) == FLASH_IBUS + SOS_SLOT_BASE + SOSIMG_TABLE_LEN,
       "user.ld: the code does not start where the image puts its bytes")
ASSERT(ADDR(.rodata) == ADDR(.text) + SIZEOF(.text) - FLASH_IBUS + FLASH_DBUS,
       "user.ld: the read-only data does not start right behind the code")
ASSERT(SOSIMG_TABLE_LEN + SIZEOF(.text) + SIZEOF(.rodata) + SIZEOF(.data)
       <= SOS_SLOT_LEN,
       "user.ld: the image does not fit its flash slot")
