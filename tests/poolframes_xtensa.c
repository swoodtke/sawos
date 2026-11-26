// Where the RAM pool's frames are, on the ESP32-S3 board: the physical address
// a pin of the pool's first allocation answers. It mirrors
// `tools/sos_runner.py`'s `pool_base` for this board, and
// `tests/poolbase_riscv32.c` says why a test carries such a number in C.

unsigned long sos_test_pool_frames(void) { return 0x3FCE0000UL; }
