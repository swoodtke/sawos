// A word-aligned constant in this image's read-only data, and its address,
// which Saw cannot name for a `const` of its own.

static const unsigned long ro_cell[64] __attribute__((aligned(16))) = {
    0x5A5A5A5AUL,
    0x5A5A5A5AUL,
};

unsigned long sos_ro_cell(void) {
    return (unsigned long)ro_cell;
}
