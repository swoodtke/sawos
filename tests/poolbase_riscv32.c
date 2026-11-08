// Where the RAM pool is, on this board: the one number a map test's config
// carries.
//
// This is C because Saw cannot name an address any other way: `extern`
// declares functions, an extern function is not usable as a value, and
// `@export` on a static emits a definition rather than a reference (the same
// gap the kernel HAL's `sink.c` is written around). A per-architecture `.c`
// file in the manifest's `native` list is the established spelling.
//
// A test knows this at all because a region is a capability, not a
// description: there is no op that reads a Memory's bounds. What a process
// knows about where its memory is, it knows from the config that handed it the
// region, as the uart-echo driver knows `UART_BASE`.
//
// It mirrors `tools/sos_runner.py`'s `pool_base` / `pool_len` for this
// architecture. The two agree rather than one deriving from the other: the
// build publishes a row and the program says which row it expects. A mismatch
// shows up as a map that installs memory the program then fails to read back,
// which is what the map cases assert.

unsigned long sos_test_pool_base(void) { return 0x80280000UL; }
unsigned long sos_test_pool_len(void)  { return 0x00040000UL; }
