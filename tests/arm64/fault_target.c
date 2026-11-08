// The address the faulting root reaches, on arm64.
//
// The kernel's text base: inside the kernel image, mapped EL1-only by the
// identity map, and outside every grant root holds. It is the one
// architecture-specific part of that test, so it lives here rather than in
// `tests/faulting-root/src/`, which stays machine-neutral Saw.

typedef unsigned long u64;

u64 sos_test_forbidden_addr(void) {
    return 0x40000000UL;
}
