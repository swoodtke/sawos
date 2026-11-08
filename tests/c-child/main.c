/* The C child's program: a `main` that returns an exit code.
 *
 * The image has no `sos` module, no `sosrt`, no arena and no Saw. It is a C
 * translation unit compiled freestanding, linked at a user image base with
 * the same script every Saw child uses, emitted as a sosimg, and ended
 * through `Process.exit`.
 *
 * It prints two lines. The first proves the image ran: code and `.rodata`
 * reached their link addresses and the console op works. The second proves
 * it computed: the count is produced by a loop, held in a stack frame, and
 * rendered by an integer division, so a build that blitted a constant string
 * cannot produce it. With the exit code the launcher reads back, these are
 * the three things "a C process really ran" means.
 *
 * There is no `printf`: the console op takes one byte, so every string is a
 * loop and every number a hand-rolled conversion (`put_uint`).
 */

#include "sos.h"

/* The greeting. `.rodata`, so it rides the image's one R+X segment and the
 * program has no writable data: `.data` and `.bss` are both empty. */
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

/* Write an unsigned decimal. Ten digits is more than a 32-bit count can need.
 * The buffer is on the stack, which proves the kernel's initial stack pointer
 * is real memory a C frame can use. */
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

    /* The exit code. Distinct from the three echo children's 41/42/43, so a
     * transcript naming it names this image; the launcher asserts
     * `Exited`(1) << 16 | 55 = 65591, a word no fault could produce. */
    return 55;
}
