// WHERE THE RAM POOL IS, on this board — the one number a map test's config
// carries (sawos design 6 D-5, the map proof cases).
//
// C BECAUSE SAW CANNOT NAME AN ADDRESS ANY OTHER WAY, and that is the same gap
// the kernel HAL's `sink.c` is written around: `extern` declares functions, an
// extern function is not usable as a value, and `@export` on a static emits a
// DEFINITION rather than a reference (DF-172a). A per-architecture `.c` file in
// the manifest's `native` list is the established spelling.
//
// WHY A TEST KNOWS THIS AT ALL. A region is a CAPABILITY, not a description:
// there is no op that reads a Memory's bounds, and unit 4 deliberately did not
// add one. What a process knows about where its memory IS, it knows from the
// config that handed it the region — root's config is what says which boot tag
// means what, and where is the same class of fact. The uart-echo driver has
// always known `UART_BASE` for exactly this reason.
//
// It MIRRORS `tools/sos_runner.py`'s `pool_base` / `pool_len` for this
// architecture. The two agree rather than one deriving from the other, which is
// the same arrangement the M2 device window had: the build publishes a row and
// the program says which row it expects. A mismatch shows up as a map that
// installs memory the program then fails to read back, which is exactly what
// the map cases assert.

unsigned long sos_test_pool_base(void) { return 0x80280000UL; }
unsigned long sos_test_pool_len(void)  { return 0x00040000UL; }
