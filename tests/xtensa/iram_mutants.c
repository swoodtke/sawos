// The PSRAM command layer with one slip added, for the runner's `check_iram`
// to refuse (the smoke's `iram_mutant_*` cases). Each mutant is the real
// `mspi.c` and one function more, named `sos_psram_mutant`, that puts
// something of the command layer where the rules forbid; the case names the
// finding the check must report. Built with exactly one of the MUT_* names
// defined. The placement rule's slip, the link script's IRAM line removed, is
// a script edit the runner makes itself (`iram_mutant_script`).

#include "../../hal/xtensa-esp32s3/kernel/mspi.c"

#if defined(MUT_TABLE)
// A constant table: `.rodata`, which is flash.
IRAM u32 sos_psram_mutant(u32 i) {
    static const u32 table[4] = {0x4040u, 0xC0C0u, 0x0000u, 0x8080u};
    return table[i & 3u];
}
#elif defined(MUT_SWITCH)
// A switch dense enough for a jump table, which the compiler puts in
// `.rodata`.
IRAM u32 sos_psram_mutant(u32 density) {
    switch (density) {
    case 0: return 0;
    case 1: return 4;
    case 2: return 2;
    case 3: return 8;
    case 4: return 1;
    case 5: return 16;
    case 6: return 64;
    case 7: return 32;
    default: return 0xFFu;
    }
}
#elif defined(MUT_HELPER)
// A 64-bit division: a call to the compiler's helper, which is not here.
IRAM u32 sos_psram_mutant(u32 hi, u32 divider) {
    unsigned long long wide = ((unsigned long long)hi << 32) | divider;
    return (u32)(wide / (divider + 1u));
}
#elif defined(MUT_MEMSET)
// A zeroed array large enough to become a call to `memset`.
IRAM u32 sos_psram_mutant(u32 i) {
    volatile u32 words[64] = {0};
    words[i & 63u] = i;
    return words[(i + 1u) & 63u];
}
#elif defined(MUT_NOIRAM)
// The bounded wait without its section: its code and literals in `.text`,
// which is flash.
u32 sos_psram_mutant(void) {
    for (u32 n = 0; n < POLL_BOUND; n++) {
        if (*(volatile u32 *)(SPI1 + SPI_CMD) == 0) {
            return 1;
        }
    }
    return 0;
}
#elif !defined(MUT_NONE)
#error "iram_mutants.c: define one MUT_* name"
#endif
