/* SOS boot, vectors and trap entry: Xtensa LX7, ESP32-S3 board, call0.
 *
 * Every image on this board is call0: one register window, rotated by nothing
 * after the reset path below gives up the window the ROM arrived in. `ktrap`
 * and every other compiled function are ordinary call0 functions (arguments
 * in a2..a7, result in a2, return address in a0, stack in a1).
 *
 * What this file owns:
 *   1. The direct-boot entry. The mask ROM checks two magic words at flash
 *      offset 0x400, maps flash at both bus windows and `callx8`s flash offset
 *      0x408 (../ABI.md §2a); `esp32s3.ld` places `.magic` and `.entry` there.
 *   2. `_start`: leave the ROM's register window, clear `CPENABLE`, quiet
 *      the watchdogs, copy the vectors and the trap path into IRAM, copy
 *      `.data`, zero `.bss`, point `VECBASE` at the table and call the Saw
 *      `kmain`.
 *   3. The vector table (../ABI.md §5a). `syscall`, level-1 interrupts and
 *      faults taken in user mode arrive at the user vector, which is the trap
 *      entry. Every other vector stops the machine through `kernel_fault`
 *      (`sink.c`). Most of them mean a kernel bug or a window exception call0
 *      never raises, but not all: a `break` executed in user mode reaches the
 *      debug vector (0x280) and stops the machine too. Compiled code traps
 *      with `ill`, never `break`, so only hand-written assembly gets there,
 *      and the Flat tier has no privilege to deny it; nor to deny `rsil`,
 *      with which a process raises its own INTLEVEL and turns the tick off
 *      for as long as it runs.
 *   4. The trap entry and the restore path. The restore path is the whole
 *      context switch and the first entry to user mode: `ktrap` returns the
 *      frame to resume and `sos_resume_frame` enters a frame `frame_init`
 *      built.
 *
 * `EXCSAVE1` holds the running thread's frame address while user mode runs.
 * The user vector swaps it with a1, so the frame is the save area at once and
 * the user's a1 waits in `EXCSAVE1` until it is stored. The kernel runs with
 * PS.UM = 0, PS.EXCM = 0 and PS.INTLEVEL = 15: no interrupt is delivered to
 * it, and a fault it takes arrives at the kernel vector (or at the double
 * vector before PS is rewritten), never at the user one, so the vector itself
 * is the mode witness.
 *
 * The frame layout (`F_*` below) is described once here and once in Saw, as
 * `lib.saw`'s `TrapFrame`, whose `static_assert` pins the size.
 *
 * Every `l32r` loads a literal from below it, so each section's literals are
 * declared at its head and `esp32s3.ld` lists `.literal*` and `.text*` in one
 * pattern, so a function follows its literals.
 */

    .equ F_PC,          0           /* EPC1: where the context resumes */
    .equ F_PS,          4
    .equ F_A0,          8           /* a0..a15 at 8 + 4*n */
    .equ F_A1,          12
    .equ F_A2,          16
    .equ F_A3,          20
    .equ F_A4,          24
    .equ F_A5,          28
    .equ F_A6,          32
    .equ F_A7,          36
    .equ F_A8,          40
    .equ F_A9,          44
    .equ F_A10,         48
    .equ F_A11,         52
    .equ F_A12,         56
    .equ F_A13,         60
    .equ F_A14,         64
    .equ F_A15,         68
    .equ F_SAR,         72
    .equ F_LBEG,        76
    .equ F_LEND,        80
    .equ F_LCOUNT,      84
    .equ F_EXCCAUSE,    88
    .equ F_EXCVADDR,    92
    .equ F_SCOMPARE1,   96
    .equ F_THREADPTR,   100

    /* The kernel's PS: INTLEVEL = 15, EXCM = 0, UM = 0, WOE = 0. */
    .equ PS_KERNEL,     0x0F

    /* The watchdog registers and their write-protect keys. ../ABI.md §2e
     * gives the source of each number. */
    .equ WDT_WKEY,              0x50D83AA1  /* RTC and TIMG watchdog key */
    .equ RTC_WDT_CONFIG0,       0x60008098
    .equ RTC_WDT_WPROTECT,      0x600080B0
    .equ SWD_CONF,              0x600080B4
    .equ SWD_WPROTECT,          0x600080B8
    .equ SWD_WKEY,              0x8F1D312A
    .equ SWD_AUTO_FEED_EN,      0x80000000  /* SWD_CONF bit 31 */
    .equ TIMG0_WDT_CONFIG0,     0x6001F048
    .equ TIMG0_WDT_WPROTECT,    0x6001F064

/* ---- the direct-boot protocol -------------------------------------------- */

    .section .magic, "a"
    .word   0xAEDB041D
    .word   0xAEDB041D

    .section .entry, "ax"
    .global _entry
_entry:
    j       _start

/* ---- reset path (flash, execute in place) -------------------------------- */

    .section .text.start, "ax"
    .literal .Liram_load, _iram_load
    .literal .Liram_start, _iram_start
    .literal .Liram_end, _iram_end
    .literal .Ldata_load, _data_load
    .literal .Ldata_start, _data_start
    .literal .Ldata_end, _data_end
    .literal .Lbss_start, _bss_start
    .literal .Lbss_end, _bss_end
    .literal .Lvecbase, _vector_base
    .literal .Lstack, _stack_top
    .literal .Lkmain, kmain
    .literal .Lwdt_key, WDT_WKEY
    .literal .Lrtc_wdt_wprotect, RTC_WDT_WPROTECT
    .literal .Lrtc_wdt_config0, RTC_WDT_CONFIG0
    .literal .Ltimg0_wdt_wprotect, TIMG0_WDT_WPROTECT
    .literal .Ltimg0_wdt_config0, TIMG0_WDT_CONFIG0
    .literal .Lswd_key, SWD_WKEY
    .literal .Lswd_wprotect, SWD_WPROTECT
    .literal .Lswd_conf, SWD_CONF
    .literal .Lswd_auto_feed, SWD_AUTO_FEED_EN
    .align  4
    .global _start
_start:
    /* The ROM arrives windowed (`callx8`, PS.WOE = 1). Leave exactly one live
     * window and turn window exceptions off. Writing WINDOWBASE renames every
     * address register, so nothing is kept in one across it; nothing needs to
     * be, since this path never returns to the ROM. */
    movi    a9, 1
    wsr     a9, windowstart
    movi    a9, 0
    wsr     a9, windowbase
    rsync
    movi    a9, PS_KERNEL
    wsr     a9, ps
    rsync

    /* The FPU off, whatever the ROM left: no image on this profile is built
     * to use it, so an FP instruction is a coprocessor-disabled fault. */
    movi    a9, 0
    wsr     a9, cpenable
    rsync

    /* The watchdogs, before anything slow (../ABI.md §2e). On silicon the ROM
     * leaves the RTC watchdog and TIMG0's watchdog running after a flash boot,
     * and the super watchdog needs feeding; any of them resets the part
     * mid-boot. The RTC and TIMG0 watchdogs are switched off whole (config
     * word 0), the super watchdog is set to feed itself, and each register is
     * unlocked with its key for the one write and locked again after. The
     * emulator models the registers and none of these writes changes what it
     * runs. */
    movi    a5, 0
    l32r    a3, .Lwdt_key
    l32r    a2, .Lrtc_wdt_wprotect
    l32r    a4, .Lrtc_wdt_config0
    memw
    s32i    a3, a2, 0
    memw
    s32i    a5, a4, 0
    memw
    s32i    a5, a2, 0
    l32r    a2, .Ltimg0_wdt_wprotect
    l32r    a4, .Ltimg0_wdt_config0
    memw
    s32i    a3, a2, 0
    memw
    s32i    a5, a4, 0
    memw
    s32i    a5, a2, 0
    l32r    a3, .Lswd_key
    l32r    a2, .Lswd_wprotect
    l32r    a4, .Lswd_conf
    l32r    a7, .Lswd_auto_feed
    memw
    s32i    a3, a2, 0
    memw
    l32i    a6, a4, 0
    or      a6, a6, a7
    s32i    a6, a4, 0
    memw
    s32i    a5, a2, 0
    memw

    /* The vectors and the trap path: flash -> IRAM. The load addresses are
     * the data-bus aliases of their flash bytes (`esp32s3.ld`), so these are
     * ordinary data loads. */
    l32r    a2, .Liram_load
    l32r    a3, .Liram_start
    l32r    a4, .Liram_end
1:  bgeu    a3, a4, 2f
    l32i    a5, a2, 0
    s32i    a5, a3, 0
    addi    a2, a2, 4
    addi    a3, a3, 4
    j       1b

    /* .data: flash -> DRAM. It must land before anything reads a mutable
     * global. */
2:  l32r    a2, .Ldata_load
    l32r    a3, .Ldata_start
    l32r    a4, .Ldata_end
3:  bgeu    a3, a4, 4f
    l32i    a5, a2, 0
    s32i    a5, a3, 0
    addi    a2, a2, 4
    addi    a3, a3, 4
    j       3b

    /* .bss, the arena and the kernel stack, a word at a time. No preemption
     * point: this runs before kmain, with every interrupt masked. */
4:  l32r    a3, .Lbss_start
    l32r    a4, .Lbss_end
    movi    a5, 0
5:  bgeu    a3, a4, 6f
    s32i    a5, a3, 0
    addi    a3, a3, 4
    j       5b

    /* The copied instructions are fetched only after this. */
6:  isync
    l32r    a9, .Lvecbase
    wsr     a9, vecbase
    movi    a9, 0
    wsr     a9, excsave1
    rsync

    /* Enter the Saw kernel on the kernel stack. It does not return: the stop
     * paths in `lib.saw` halt. If it ever does, spin rather than run past the
     * image. */
    l32r    a1, .Lstack
    l32r    a0, .Lkmain
    callx0  a0
7:  j       7b

/* ---- vectors and the trap path (IRAM) ------------------------------------ */

    .section .text.vectors, "ax"
    .literal .Lkstack, _stack_top
    .literal .Lktrap, ktrap
    .literal .Lfault, kernel_fault

    /* VECBASE is the table's first byte. The ISA wants it 1 KiB aligned. */
    .align  1024
    .global _vector_base
_vector_base:
    .org    0x000
    movi    a2, 0x000               /* WindowOverflow4 */
    j       vec_fatal
    .org    0x040
    movi    a2, 0x040               /* WindowUnderflow4 */
    j       vec_fatal
    .org    0x080
    movi    a2, 0x080               /* WindowOverflow8 */
    j       vec_fatal
    .org    0x0C0
    movi    a2, 0x0C0               /* WindowUnderflow8 */
    j       vec_fatal
    .org    0x100
    movi    a2, 0x100               /* WindowOverflow12 */
    j       vec_fatal
    .org    0x140
    movi    a2, 0x140               /* WindowUnderflow12 */
    j       vec_fatal
    .org    0x180
    movi    a2, 0x180               /* Level 2 */
    j       vec_fatal
    .org    0x1C0
    movi    a2, 0x1C0               /* Level 3 */
    j       vec_fatal
    .org    0x200
    movi    a2, 0x200               /* Level 4 */
    j       vec_fatal
    .org    0x240
    movi    a2, 0x240               /* Level 5 */
    j       vec_fatal
    .org    0x280
    movi    a2, 0x280               /* Debug */
    j       vec_fatal
    .org    0x2C0
    movi    a2, 0x2C0               /* NMI */
    j       vec_fatal
    .org    0x300
    movi    a2, 0x300               /* Kernel: a trap taken with PS.UM = 0 */
    j       vec_fatal
    .org    0x340
    xsr     a1, excsave1            /* User: a1 = frame, EXCSAVE1 = user a1 */
    j       trap_entry
    .org    0x3C0
    movi    a2, 0x3C0               /* Double: a trap taken with PS.EXCM = 1 */
    j       vec_fatal
    .org    0x400

/* A trap the kernel took: report it and stop. There is no context worth
 * saving, and the registers that say what happened are read before anything
 * can disturb them. `kernel_fault(vector, exccause, epc1, excvaddr, depc)`
 * never returns. */
vec_fatal:
    rsr     a3, exccause
    rsr     a4, epc1
    rsr     a5, excvaddr
    rsr     a6, depc
    l32r    a1, .Lkstack
    l32r    a0, .Lfault
    callx0  a0
1:  j       1b

/* A syscall, a level-1 interrupt or a fault, from user mode. a1 is the
 * running thread's frame. */
trap_entry:
    s32i    a0, a1, F_A0
    s32i    a2, a1, F_A2
    s32i    a3, a1, F_A3
    s32i    a4, a1, F_A4
    s32i    a5, a1, F_A5
    s32i    a6, a1, F_A6
    s32i    a7, a1, F_A7
    s32i    a8, a1, F_A8
    s32i    a9, a1, F_A9
    s32i    a10, a1, F_A10
    s32i    a11, a1, F_A11
    s32i    a12, a1, F_A12
    s32i    a13, a1, F_A13
    s32i    a14, a1, F_A14
    s32i    a15, a1, F_A15
    rsr     a2, excsave1
    s32i    a2, a1, F_A1
    rsr     a2, epc1
    s32i    a2, a1, F_PC
    rsr     a2, ps
    s32i    a2, a1, F_PS
    rsr     a2, sar
    s32i    a2, a1, F_SAR
    rsr     a2, lbeg
    s32i    a2, a1, F_LBEG
    rsr     a2, lend
    s32i    a2, a1, F_LEND
    rsr     a2, lcount
    s32i    a2, a1, F_LCOUNT
    rsr     a2, exccause
    s32i    a2, a1, F_EXCCAUSE
    rsr     a2, excvaddr
    s32i    a2, a1, F_EXCVADDR
    rsr     a2, scompare1
    s32i    a2, a1, F_SCOMPARE1
    rur     a2, threadptr
    s32i    a2, a1, F_THREADPTR

    /* The zero-overhead loop is address-triggered: with LCOUNT non-zero,
     * kernel code reaching the user's LEND address (shared code in a flat
     * image) would branch to the user's LBEG. */
    movi    a2, 0
    wsr     a2, lcount

    /* Become the kernel. The context is saved, so EXCM can drop: a fault from
     * here on arrives at the kernel vector with its own EPC1, and INTLEVEL 15
     * keeps every interrupt pending until user mode runs again. */
    movi    a2, PS_KERNEL
    wsr     a2, ps
    rsync

    /* ktrap(frame, cause, faulting address) on the kernel stack. It returns
     * the frame to resume, which is this one or another thread's. */
    mov     a2, a1
    l32i    a3, a1, F_EXCCAUSE
    l32i    a4, a1, F_EXCVADDR
    l32r    a1, .Lkstack
    l32r    a0, .Lktrap
    callx0  a0
    /* Fall through: resume the frame in a2. */

/* void sos_resume_frame(u32 frame) -- never returns.
 *
 * Restore the context in `frame` and `rfe` into it. The frame's PS carries
 * EXCM = 1 (a saved PS always does, and `frame_init` builds one that does),
 * so writing it keeps interrupts masked until `rfe`, which clears EXCM and
 * resumes at the frame's PC with its UM and INTLEVEL. Re-arming EXCSAVE1 with
 * the frame makes the next trap save into the thread that is actually
 * running. */
    .global sos_resume_frame
sos_resume_frame:
    mov     a1, a2
    l32i    a2, a1, F_SAR
    wsr     a2, sar
    l32i    a2, a1, F_LBEG
    wsr     a2, lbeg
    l32i    a2, a1, F_LEND
    wsr     a2, lend
    l32i    a2, a1, F_SCOMPARE1
    wsr     a2, scompare1
    l32i    a2, a1, F_THREADPTR
    wur     a2, threadptr
    l32i    a2, a1, F_PC
    wsr     a2, epc1
    l32i    a2, a1, F_PS
    wsr     a2, ps
    isync
    /* LCOUNT last, with the frame's PS and its EXCM in place: the loop-back
     * at LEND is taken only with EXCM clear, so from here to `rfe` a live
     * count cannot send this code to the user's LBEG, which the entry's own
     * zeroing guards against on the way in. */
    l32i    a2, a1, F_LCOUNT
    wsr     a2, lcount
    isync
    l32i    a0, a1, F_A0
    l32i    a2, a1, F_A2
    l32i    a3, a1, F_A3
    l32i    a4, a1, F_A4
    l32i    a5, a1, F_A5
    l32i    a6, a1, F_A6
    l32i    a7, a1, F_A7
    l32i    a8, a1, F_A8
    l32i    a9, a1, F_A9
    l32i    a10, a1, F_A10
    l32i    a11, a1, F_A11
    l32i    a12, a1, F_A12
    l32i    a13, a1, F_A13
    l32i    a14, a1, F_A14
    l32i    a15, a1, F_A15
    wsr     a1, excsave1
    l32i    a1, a1, F_A1            /* user a1 last: it retires the frame */
    rfe
