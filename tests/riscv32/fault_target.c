// Where the faulting root reaches, on Profile A (designs 140, 162).
//
// The kernel's text base: inside the kernel image, outside every grant root
// holds. The address is the ONE architecture-specific thing about that test, so
// it lives here beside the profile's other hand-written fixtures rather than in
// `sos/tests/faulting-root/src/`, which stays ordinary Saw that names no
// machine — exactly like the real root server it is a copy of.

typedef unsigned int u32;

u32 sos_test_forbidden_addr(void) {
    return 0x80000000u;
}
