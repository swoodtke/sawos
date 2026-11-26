// Where the RAM pool's frames are, on this board: the physical address a pin
// of the pool's first allocation answers. It mirrors `tools/sos_runner.py`'s
// `pool_base` for this architecture, and `tests/poolbase_riscv32.c` says why a
// test carries such a number in C.
//
// `tests/poolbase_arm64.c` answers where a mapped page of the pool appears,
// which on this board is another address: a mapping is placed by the kernel.

unsigned long sos_test_pool_frames(void) { return 0x40280000UL; }
