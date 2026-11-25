// The ESP32-S3's word increments for the `psram-stress` case: the
// compare-and-swap loop on `s32c1i`, the plain load-and-store it is held
// against, and one `s32c1i` alone. C, so the loop is exactly the sequence
// the case is about and nothing a compiler's own atomics would choose.
//
// u32 sos_stress_cas(u32 addr, u32 spin, u32 bound)
//
//   One increment of the word at `addr`: load it, spin `spin` times, write
//   the loaded value to SCOMPARE1 and `s32c1i` the loaded value plus one.
//   The word is stored only if it still equals the loaded value, and the
//   instruction answers the word as it was; on a mismatch the loop starts
//   again from the load. Answers the retries the increment took, or `bound`
//   when it has retried `bound` times without a swap, and the word is then
//   not incremented.
//
// void sos_stress_plain(u32 addr, u32 spin)
//
//   The same increment with a plain store in place of `s32c1i`: load, spin,
//   store the loaded value plus one, whatever the word holds by then.
//
// u32 sos_stress_swap(u32 addr, u32 expected, u32 v)
//
//   One `s32c1i` of `v` at `addr` against `expected`; answers the word as
//   it was.
//
// The spin widens the window between the load and the store, so a tick
// lands inside it often: it is a counted loop of a decrement and a branch,
// at least one pass, and touches no memory. On the emulator its branch also
// ends a translated block, so an interrupt can be taken there.

typedef unsigned int u32;

static void spin_for(u32 n) {
    __asm__ volatile("1:\n"
                     "    addi %0, %0, -1\n"
                     "    bnez %0, 1b\n"
                     : "+a"(n));
}

static u32 swap(volatile u32 *word, u32 expected, u32 v) {
    __asm__ volatile("    wsr    %2, scompare1\n"
                     "    s32c1i %0, %1, 0\n"
                     "    memw\n"
                     : "+a"(v)
                     : "a"(word), "a"(expected)
                     : "memory");
    return v;
}

u32 sos_stress_cas(u32 addr, u32 spin, u32 bound) {
    volatile u32 *word = (volatile u32 *)addr;
    u32 retries = 0;
    for (;;) {
        u32 old = *word;
        spin_for(spin);
        if (swap(word, old, old + 1) == old) {
            return retries;
        }
        retries += 1;
        if (retries == bound) {
            return bound;
        }
    }
}

void sos_stress_plain(u32 addr, u32 spin) {
    volatile u32 *word = (volatile u32 *)addr;
    u32 old = *word;
    spin_for(spin);
    *word = old + 1;
}

u32 sos_stress_swap(u32 addr, u32 expected, u32 v) {
    return swap((volatile u32 *)addr, expected, v);
}
