// Where the RAM pool's frames are, on this board: the physical address a pin
// of the pool's first allocation answers. It mirrors `tools/sos_runner.py`'s
// `pool_base` for this architecture, and `tests/poolbase_riscv32.c` says why a
// test carries such a number in C.
//
// It is `sos_test_pool_base` on this tier, where a mapping's address is its
// memory's own; it is its own function because on arm64 the two differ.

unsigned long sos_test_pool_frames(void) { return 0x80280000UL; }
