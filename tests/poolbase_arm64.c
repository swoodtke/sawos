// WHERE A MAPPED PAGE OF THE RAM POOL APPEARS, on this board — see
// `poolbase_riscv32.c` beside this file for the whole argument (sawos design 6
// D-5, the map proof cases).
//
// **THE NUMBER IS NOW A VIRTUAL ADDRESS, AND THE NAME IS THE STALE HALF**
// (sawos design 33, M5 unit 2). It used to be the pool's PHYSICAL base
// (0x4028_0000), which was also the address a mapping of the pool's front
// appeared at, because a user address on this board WAS its physical address.
// Placement split those: the pool's frames are still at 0x4028_0000 and
// `tools/sos_runner.py`'s `pool_base` still says so, but a process that maps
// them sees them wherever the KERNEL put them.
//
// Which is here. The kernel's VA policy is first fit at or above the mapping
// process's own region top (`kcore.process.free_user_va`), every image on this
// board links at `hal.USER_IMAGE_BASE` = 0x4020_0000, and a region is 256 KiB —
// so a process's first mapping lands at 0x4024_0000, root and child alike. That
// uniformity is what lets ONE constant serve `share-double-map` and
// `child-share`, which map one page into two address spaces and each read the
// other's byte through their own row.
//
// SO THE FUNCTION'S NAME LIES A LITTLE, AND IT LIES ON PURPOSE. Renaming it
// would mean editing ten test packages that are compiled for BOTH architectures,
// and this unit's gate requires the riscv32 half of the transcript to be
// byte-identical — a linker script and a per-architecture `native` file are the
// only arm64-only levers a shared test source leaves. The riscv32 file beside
// this one still returns the pool's physical base and is still telling the whole
// truth there, because on that tier the two answers are one answer.
//
// THE PROPERLY-SPELLED VERSION EXISTS: `Mapping.base()` asks the kernel where a
// row landed, needs no build-time constant and is right on every tier. The
// `map_placed` case is written against it. Migrating the ten older packages onto
// it is recorded as owed work rather than done here.
//
// `sos_test_pool_len` is UNMOVED: a length is a length on any tier, and
// `memory-recycle` does arithmetic with it rather than dereferencing it.

unsigned long sos_test_pool_base(void) { return 0x40240000UL; }
unsigned long sos_test_pool_len(void)  { return 0x00040000UL; }
