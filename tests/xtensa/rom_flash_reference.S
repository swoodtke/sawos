/* The IRAM half of `rom_flash_reference.saw`: one call0 function in an
 * `.iram.text.*` section, which `esp32s3.ld` places in `.iram`, holding the
 * ROM's `esp_rom_spiflash_config_readmode` jump-table entry as a word after
 * its return.
 *
 * u32 sos_romref_iram(void): answers 0.
 */

    .section .iram.text.sos_romref, "ax"
    .align 4
    .global sos_romref_iram
    .type sos_romref_iram, @function
sos_romref_iram:
    movi a2, 0
    ret
    .align 4
    .word 0x40000aa4
    .size sos_romref_iram, . - sos_romref_iram
