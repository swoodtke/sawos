// The child's pattern, as initialized data.
//
// It is C because Saw cannot name the address of a static, and because an
// array handed out as an address is one the compiler cannot assume stays what
// it was initialized to across the system calls between two reads: the
// launcher may change it, and the child's verdict is whether it did.
//
// The four words appear nowhere else in an image. `tests/split-live-bytes`
// looks for them in a region it was given, and mirrors these values.

static unsigned int witness[4] = {
    0x51ABC0DEu, 0x0BADF00Du, 0x13579BDFu, 0x2468ACE0u,
};

unsigned long sos_test_witness(void) { return (unsigned long)witness; }
