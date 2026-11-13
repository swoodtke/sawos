/* SOS kernel linker script: Xtensa, ESP32-S3 board.
 *
 * ---------------------------------------------------------------------------
 * Where things run
 * ---------------------------------------------------------------------------
 * Code executes in place from the flash instruction window; read-only data is
 * read through the flash data window; only the vectors and the trap path, the
 * kernel's `.data` and its `.bss` live in SRAM. `boot.S` copies the first two
 * out of flash and zeroes the third.
 *
 * The mask ROM maps flash offset 0 at 0x4200_0000 (IBUS, instructions) and at
 * 0x3C00_0000 (DBUS, data) before it jumps in (../ABI.md §2a). This script
 * splits the image between them by flash offset: the first 2 MiB hold
 * everything fetched as instructions, and the load images of the vectors and
 * of `.data`, which `boot.S` copies out; the second 2 MiB hold the read-only
 * data that is read in place. That DBUS section is linked at its data-bus
 * address with its load address at the same offset in the instruction
 * window, which is where the runner writes it into the flash image (LMA -
 * 0x4200_0000).
 *
 *   flash 0x00_0000  0x4200_0000  (0xFF)    the ROM reads byte 0: not 0xE9
 *   flash 0x00_0400  0x4200_0400  .magic    two words of 0xAEDB041D
 *   flash 0x00_0408  0x4200_0408  .entry    the ROM `callx8`s here
 *         ...                     .iram, .data load images; .text; .payload
 *   flash 0x20_0000  0x3C20_0000  .drom     .rodata, .regions, .childimg
 *
 * The two load images that `boot.S` copies are read through their DBUS
 * aliases (`_iram_load`, `_data_load`), so the copy is a data load on the
 * data bus. Literal pools stay beside their code in IBUS: `l32r` is the one
 * data load that reads the instruction window (../ABI.md §1c, §3c).
 *
 * `.payload` stays in IBUS because a hand-assembled user payload executes in
 * place there. A root sosimg in it is read by the loader through the same
 * addresses; ../ABI.md §11 lists data loads from IBUS among the things
 * silicon may refuse.
 *
 * ---------------------------------------------------------------------------
 * SRAM
 * ---------------------------------------------------------------------------
 * On the emulator IRAM and DRAM are two arrays; on silicon most of both are
 * one SRAM seen through two buses, IRAM 0x4037_8000 + n being DRAM
 * 0x3FC8_8000 + n (../ABI.md §3a). The ranges below stay disjoint under
 * either reading, so a layout that works here does not corrupt itself there:
 *
 *   IRAM 0x4037_8000 - 0x4037_BFFF  vectors + trap path   (DRAM twin
 *                                   0x3FC8_8000 - 0x3FC8_BFFF, unused)
 *   DRAM 0x3FC8_C000 - 0x3FCA_FFFF  kernel .data + .bss   (IRAM twin
 *                                   0x4037_C000 - 0x4039_FFFF, unused)
 *   IRAM 0x403A_0000 - 0x403D_FFFF  the process regions   (DRAM twin
 *                                   0x3FCB_0000 - 0x3FCE_FFFF, unused)
 *
 * 0x4037_0000 - 0x4037_7FFF is left alone (../ABI.md §3a). The ROM's own
 * RAM, 0x3FCD_7E04 - 0x3FCE_FFFF, holds the stack the ROM enters on; it falls
 * in the process regions' twin, which nothing touches until a process is
 * loaded, long after `_start` has left the ROM's stack. The process regions
 * are not set here: `../user/root.ld`, `../user/child.ld` and `lib.saw`
 * (`ROOT_LOAD_BASE`, `RAM_BASE`) own them.
 *
 * The kernel's .data + .bss must fit `DRAM`. An overshoot is a link error
 * ("will not fit in region"), never a silent overlap; `ARENA_SIZE` and
 * `KERNEL_STACK_SIZE` are the levers.
 */

ENTRY(_entry)

/* `sosrt`'s bump-allocator arena (hal/riscv32/kernel/virt.ld describes it in
 * full). The kernel allocates nothing on its steady path; the arena backs the
 * compiler's panic path only. The boot stack is the other boards' size. */
PROVIDE(ARENA_SIZE = 16K);
KERNEL_STACK_SIZE = 64K;

MEMORY {
    /* Instruction-bus half of flash. */
    FLASH (rx)  : ORIGIN = 0x42000000, LENGTH = 2M

    /* Data-bus half of flash: DROM is where it is read, FLASH_D is the same
     * bytes as the instruction window names them, for the load address. */
    DROM (r)    : ORIGIN = 0x3C200000, LENGTH = 2M
    FLASH_D (r) : ORIGIN = 0x42200000, LENGTH = 2M

    IRAM (rwx)  : ORIGIN = 0x40378000, LENGTH = 16K
    DRAM (rw)   : ORIGIN = 0x3FC8C000, LENGTH = 144K
}

/* Flash offset 0 in each window. */
FLASH_IBUS = 0x42000000;
FLASH_DBUS = 0x3C000000;

SECTIONS {
    /* The direct-boot magic, then the instruction the ROM enters at. */
    .magic 0x42000400 : { KEEP(*(.magic)) } > FLASH
    .entry 0x42000408 : { KEEP(*(.entry)) } > FLASH

    /* The vectors and the trap path. Listed ahead of `.text` so its literal
     * and code wildcards cannot claim them. `boot.S` aligns the table itself
     * to 1 KiB, behind the literals the trap path loads. */
    .iram : ALIGN(4) {
        _iram_start = .;
        *(.literal.vectors)
        KEEP(*(.text.vectors))
        . = ALIGN(4);
        _iram_end = .;
    } > IRAM AT> FLASH
    _iram_load = LOADADDR(.iram) - FLASH_IBUS + FLASH_DBUS;

    /* Every literal pool ahead of every function: `l32r` reaches only
     * backwards, up to 256 KiB (../ABI.md §1c). The reset path's pool and
     * code lead so they sit next to `.entry`. */
    .text : ALIGN(4) {
        *(.literal.start) *(.text.start)
        *(.literal .literal.*)
        *(.text .text.*)
    } > FLASH

    /* The one section that moves at boot: VMA in DRAM, load image in
     * flash. */
    .data : ALIGN(4) {
        _data_start = .;
        *(.data .data.*)
        . = ALIGN(4);
        _data_end = .;
    } > DRAM AT> FLASH
    _data_load = LOADADDR(.data) - FLASH_IBUS + FLASH_DBUS;

    /* The appended root image or user payload, bounded by the symbols
     * `sink.c` reads back. Empty (start == end) in an image built without
     * one. */
    .payload : ALIGN(4096) {
        _payload_start = .;
        KEEP(*(.payload))
        . = ALIGN(4096);
        _payload_end = .;
    } > FLASH

    /* Read-only data, the boot region table and the child images it names,
     * all read as data. One output section, so its VMA and LMA differ by the
     * one constant the ASSERT below checks. */
    .drom : ALIGN(16) {
        *(.rodata .rodata.*)
        . = ALIGN(8);
        _region_table_start = .;
        KEEP(*(.regions))
        _region_table_end = .;
        . = ALIGN(16);
        KEEP(*(.childimg))
    } > DROM AT> FLASH_D

    .bss (NOLOAD) : ALIGN(16) {
        _bss_start = .;
        *(.bss .bss.*)
        *(COMMON)

        /* The arena and the boot stack, inside `.bss` so `boot.S`'s zero
         * loop covers them. */
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
    } > DRAM

    /DISCARD/ : {
        *(.comment)
        *(.xtensa.info)
        *(.xt.prop .xt.prop.*)
        *(.xt.lit .xt.lit.*)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}

ASSERT(ADDR(.drom) - FLASH_DBUS == LOADADDR(.drom) - FLASH_IBUS,
       "esp32s3.ld: .drom's data-bus address must name its own flash bytes")
