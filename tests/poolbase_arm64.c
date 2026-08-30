// WHERE THE RAM POOL IS, on this board — see `poolbase_riscv32.c` beside this
// file for the whole argument (sawos design 6 D-5, the map proof cases).
//
// The number is CONSTRAINED here as well as chosen: EL0 can only be granted
// pages inside the HAL's 4 MiB grant window, so the pool has to sit between the
// child region's top (0x4028_0000) and 0x4040_0000. It does.
//
// It MIRRORS `tools/sos_runner.py`'s `pool_base` / `pool_len` for this
// architecture.

unsigned long sos_test_pool_base(void) { return 0x40280000UL; }
unsigned long sos_test_pool_len(void)  { return 0x00040000UL; }
