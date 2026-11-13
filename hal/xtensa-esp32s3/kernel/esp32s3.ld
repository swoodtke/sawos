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
 * lays the image out as one contiguous run of flash: first everything fetched
 * as instructions, with the load images of the vectors and of `.data`, which
 * `boot.S` copies out, all linked in the instruction window; then everything
 * read in place as data, linked at the data-bus address of the flash bytes
 * that follow. Each section's load address is its offset in the instruction
 * window, which is where the runner writes it into the flash image (LMA -
 * 0x4200_0000).
 *
 *   flash 0x00_0000  0x4200_0000  (0xFF)    the ROM reads byte 0: not 0xE9
 *   flash 0x00_0400  0x4200_0400  .magic    two words of 0xAEDB041D
 *   flash 0x00_0408  0x4200_0408  .entry    the ROM `callx8`s here
 *         ...                     .iram, .data load images; .text; a
 *                                 hand-assembled user payload
 *   flash N          0x3C00_0000 + N   .drom   .rodata, a root image,
 *                                      .regions, .childimg
 *
 * The two load images that `boot.S` copies are read through their DBUS
 * aliases (`_iram_load`, `_data_load`), so the copy is a data load on the
 * data bus, and so is everything the loader reads out of a root or child
 * image. Literal pools stay beside their code in IBUS: `l32r` is the one data
 * load that reads the instruction window (../ABI.md §1c, §3c). A
 * hand-assembled payload stays in IBUS because it executes in place.
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

/* Whether `sos_sim_exit` executes `simcall`. The emulator answers it under
 * `-semihosting`; on a board no debugger does, so a board image is linked
 * with `--defsym=_sos_semihosting=0` and its stops end in a quiet loop
 * instead (../ABI.md §10). */
PROVIDE(_sos_semihosting = 1);

MEMORY {
    /* The instruction-bus window over the image's flash. The data-bus
     * sections are placed by address below, at the same offsets. */
    FLASH (rx)  : ORIGIN = 0x42000000, LENGTH = 4M

    IRAM (rwx)  : ORIGIN = 0x40378000, LENGTH = 16K
    DRAM (rw)   : ORIGIN = 0x3FC8C000, LENGTH = 144K
}

/* Flash offset 0 in each window. */
FLASH_IBUS = 0x42000000;
FLASH_DBUS = 0x3C000000;
FLASH_BYTES = 4M;

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

    /* A hand-assembled user payload, which executes in place: an executable
     * `.payload` input section. A root image's `.payload` is data and goes to
     * `.drom` below. */
    .payload_code : ALIGN(16) {
        _payload_code_start = .;
        KEEP(INPUT_SECTION_FLAGS(SHF_EXECINSTR) *(.payload))
        . = ALIGN(16);
        _payload_code_end = .;
    } > FLASH

    /* Read-only data, the appended root image, the boot region table and the
     * child images it names: everything the kernel only reads. It starts at
     * the next free flash offset after the instruction-bus sections and is
     * linked at that offset's data-bus address, so the image is one
     * contiguous run of flash. One output section, so its VMA and LMA differ
     * by the one constant the ASSERT below checks.
     *
     * The start is page-aligned. It is the image's lowest address, and the
     * linker loads the ELF and program headers into the page below the
     * lowest section when they fit there, which would put a segment outside
     * the flash window; at a page boundary they never fit. */
    _drom_flash = ALIGN(LOADADDR(.payload_code) + SIZEOF(.payload_code), 4096);
    .drom (_drom_flash - FLASH_IBUS + FLASH_DBUS) : AT(_drom_flash) {
        *(.rodata .rodata.*)
        . = ALIGN(16);
        _payload_data_start = .;
        KEEP(INPUT_SECTION_FLAGS(!SHF_EXECINSTR) *(.payload))
        . = ALIGN(16);
        _payload_data_end = .;
        . = ALIGN(8);
        _region_table_start = .;
        KEEP(*(.regions))
        _region_table_end = .;
        . = ALIGN(16);
        KEEP(*(.childimg))
    }

    /* The payload `sink.c` reports: whichever of the two an image carries.
     * Empty (start == end) in an image built with neither. */
    _payload_start = _payload_code_end > _payload_code_start ?
                     _payload_code_start : _payload_data_start;
    _payload_end = _payload_code_end > _payload_code_start ?
                   _payload_code_end : _payload_data_end;

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
ASSERT(LOADADDR(.drom) + SIZEOF(.drom) <= FLASH_IBUS + FLASH_BYTES,
       "esp32s3.ld: the image does not fit the flash it is written to")
