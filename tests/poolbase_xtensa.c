// Where the RAM pool is, on the ESP32-S3 board: the one number a map test's
// config carries. `tests/poolbase_riscv32.c` says why this is C and why a
// program knows it at all.
//
// It mirrors `tools/sos_runner.py`'s `pool_base` / `pool_len` for this board
// and the pool row `hal/xtensa-esp32s3/kernel/lib.saw` lays out above the
// child region.

unsigned long sos_test_pool_base(void) { return 0x3FCE0000UL; }
unsigned long sos_test_pool_len(void)  { return 0x00010000UL; }
