/* SOS from C: the program (sawos design 39, the C-leg probe).
 *
 * **A `main` THAT RETURNS AN EXIT CODE, AND NOTHING ELSE ABOUT IT IS SPECIAL.**
 * That is the whole claim of the unit: no `sos` module, no `sosrt`, no arena,
 * no Saw in the image at all — a C translation unit compiled freestanding by
 * the same clang the kernel's `sink.c` goes through, linked at a user image
 * base by the same `ld.lld` with the same script every Saw child uses, emitted
 * as the same sosimg, loaded by the same loader, scheduled by the same
 * scheduler, and ended through the same `Process.exit`.
 *
 * WHAT THE TWO LINES IT PRINTS ARE FOR. The first proves the image RAN — code
 * and `.rodata` reached their link addresses and the console op works. The
 * second proves it COMPUTED: the count is produced by a loop, held in a stack
 * frame, and rendered by an integer division the compiler lowered for this
 * target — so a build that merely blitted a constant string cannot produce it.
 * Together with the exit code the launcher reads back, they are the three
 * things "a C process really ran" means.
 *
 * THERE IS NO `printf`, AND THE SIX LINES OF `put_uint` BELOW ARE WHY design 31
 * part 2 sizes a libc shim rather than a header (design 39 finding 6): the
 * console op takes ONE BYTE, so every string is a loop and every number is a
 * hand-rolled conversion.
 */

#include "sos.h"

/* The greeting. `.rodata`, so it rides the image's one R+X segment and the
 * program has no writable data at all — which is what makes this image's
 * `.data` and `.bss` both empty. */
static const char GREETING[] = "SOS cchild: hello from freestanding C\n";

/* Write a NUL-terminated string, one syscall per byte, and answer how many
 * bytes went out. */
static unsigned put_str(sos_word system, const char *s) {
    unsigned n = 0;
    while (s[n] != '\0') {
        (void)sos_system_debug_print(system, (sos_word)(unsigned char)s[n]);
        n = n + 1;
    }
    return n;
}

/* Write an unsigned decimal. Ten digits is more than a 32-bit count can need
 * and the buffer is on the stack, which is the other half of what this program
 * proves: the kernel's initial stack pointer is real memory a C frame can use. */
static void put_uint(sos_word system, unsigned value) {
    char digits[10];
    int at = 0;
    if (value == 0) {
        (void)sos_system_debug_print(system, (sos_word)'0');
        return;
    }
    while (value != 0 && at < 10) {
        digits[at] = (char)('0' + (value % 10));
        value = value / 10;
        at = at + 1;
    }
    while (at > 0) {
        at = at - 1;
        (void)sos_system_debug_print(system, (sos_word)(unsigned char)digits[at]);
    }
}

int main(sos_word boot_handle) {
    unsigned wrote = put_str(boot_handle, GREETING);

    (void)put_str(boot_handle, "SOS cchild: wrote ");
    put_uint(boot_handle, wrote);
    (void)put_str(boot_handle, " bytes\n");

    /* THE EXIT CODE. Distinct from the three echo children's 41/42/43, so a
     * transcript naming it names THIS image; the launcher asserts
     * `Exited`(1) << 16 | 55 = 65591, a word no fault could produce. */
    return 55;
}
