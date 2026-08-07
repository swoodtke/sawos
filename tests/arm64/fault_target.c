// Where the faulting root reaches, on Profile B (design 162).
//
// The kernel's text base: inside the kernel image, mapped EL1-only by the
// identity map, and outside every grant root holds. The address is the ONE
// architecture-specific thing about that test, so it lives here beside the
// profile's other hand-written fixtures rather than in
// `sos/tests/faulting-root/src/`, which stays ordinary Saw that names no
// machine — exactly like the real root server it is a copy of.

typedef unsigned long u64;

u64 sos_test_forbidden_addr(void) {
    return 0x40000000UL;
}
