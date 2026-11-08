// Where a mapped page of the RAM pool appears, on this board. See
// `poolbase_riscv32.c` beside this file for why a test knows this at all.
//
// The number is a virtual address, so the function name is the stale half. The
// pool's frames are at 0x4028_0000 (`tools/sos_runner.py`'s `pool_base`), but a
// process that maps them sees them wherever the kernel put them. The kernel's
// VA policy is first fit at or above the mapping process's own region top
// (`kcore.process.free_user_va`), every image on this board links at
// `hal.USER_IMAGE_BASE`. In `share-double-map` and `child-share`, which map one
// page into two address spaces and each read the other's byte through their own
// row, the two processes' regions are the same size, so the first mapping lands
// at the same address in root and child alike. That is what lets one constant
// serve both.
//
// The name stays because these sources are shared with riscv32, whose file
// still returns the pool's physical base: on that tier the two answers are one
// answer. A linker script and a per-architecture `native` file are the only
// arm64-only levers a shared test source leaves.
//
// `Mapping.base()` asks the kernel where a row landed, needs no build-time
// constant and is right on every tier; `map-placed` is written against it.
//
// `sos_test_pool_len` is a length on any tier, and `memory-recycle` does
// arithmetic with it rather than dereferencing it.

unsigned long sos_test_pool_base(void) { return 0x40240000UL; }
unsigned long sos_test_pool_len(void)  { return 0x00040000UL; }
