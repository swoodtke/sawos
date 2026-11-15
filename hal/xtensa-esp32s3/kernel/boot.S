/* SOS boot, vectors and trap entry: Xtensa LX7, ESP32-S3 board.
 *
 * The kernel is call0: it runs on one register window with PS.WOE = 0, and
 * rotates the window only on the trap path below and in
 * `sos_windowed_call`, its one way into windowed code. `ktrap` and every other
 * compiled kernel function are ordinary call0 functions (arguments in
 * a2..a7, result in a2, return address in a0, stack in a1). A process may be
 * call0 or windowed (../ABI.md §7d): the window exceptions a windowed thread
 * raises are handled at their vectors, in the thread's own context, without
 * entering the kernel.
 *
 * What this file owns:
 *   1. The direct-boot entry. The mask ROM checks two magic words at flash
 *      offset 0x400, maps flash at both bus windows and `callx8`s flash offset
 *      0x408 (../ABI.md §2a); `esp32s3.ld` places `.magic` and `.entry` there.
 *   2. `_start`: leave the ROM's register window, clear `CPENABLE`, quiet
 *      the watchdogs, copy the vectors and the trap path into IRAM, copy
 *      `.data`, zero `.bss`, point `VECBASE` at the table and call the Saw
 *      `kmain`.
 *   3. The vector table (../ABI.md §5a). The six window vectors spill and
 *      fill a windowed thread's frames on its own stack. `syscall`, level-1
 *      interrupts and faults taken in user mode arrive at the user vector,
 *      which is the trap entry, apart from the Alloca exception, which it
 *      handles in place. A double exception inside a window handler that a
 *      user thread raised is that thread's fault and enters the kernel as
 *      one. Every other vector stops the machine through `kernel_fault`
 *      (`sink.c`). Most of them mean a kernel bug, but not all: a `break`
 *      executed in user mode reaches the debug vector (0x280) and stops the
 *      machine too. Compiled code traps with `ill`, never `break`, so only
 *      hand-written assembly gets there, and the Flat tier has no privilege
 *      to deny it; nor to deny `rsil`, with which a process raises its own
 *      INTLEVEL and turns the tick off for as long as it runs.
 *   4. The trap entry and the restore path. The restore path is the whole
 *      context switch and the first entry to user mode: `ktrap` returns the
 *      frame to resume and `sos_resume_frame` enters a frame `frame_init`
 *      built.
 *   5. `sos_clock_store`, the one store that moves the system clock, which
 *      runs from IRAM (`lib.saw`'s `clock_init`).
 *   6. `sos_windowed_call`, by which the kernel calls a windowed function
 *      (one in the mask ROM) before the loader runs.
 *   7. `sos_pll_bringup`, which powers the BBPLL and sets it to 480 MHz
 *      without a fetch through the flash cache (the `pll80` clock key).
 *
 * `EXCSAVE1` holds the running thread's frame address while user mode runs.
 * The user vector swaps it with a1, so the frame is the save area at once and
 * the user's a1 waits in `EXCSAVE1` until it is stored. The kernel runs with
 * PS.UM = 0, PS.EXCM = 0, PS.WOE = 0 and PS.INTLEVEL = 15: no interrupt is
 * delivered to it, no window exception is raised in it, and a fault it takes
 * arrives at the kernel vector (or at the double vector before PS is
 * rewritten), never at the user one, so the vector itself is the mode
 * witness.
 *
 * The window rules the handlers rely on (../ABI.md §7d): physical register
 * `aN` is AR[(4 * WINDOWBASE + N) mod 64]; WINDOWSTART has a bit per pane of
 * four registers, set where a live frame begins; with PS.EXCM = 1 no window
 * exception is raised, whatever WINDOWBASE and WINDOWSTART hold.
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
    .equ F_WINDOWBASE,  104
    .equ F_WINDOWSTART, 108
    /* The other 48 physical registers: word k is AR[(4 * WINDOWBASE + 16 + k)
     * mod 64], panes WINDOWBASE + 4 to + 15 in order. */
    .equ F_AR,          112

    /* The kernel's PS: INTLEVEL = 15, EXCM = 0, UM = 0, WOE = 0. */
    .equ PS_KERNEL,     0x0F

    .equ EXCCAUSE_ALLOCA, 5
    .equ PS_UM_BIT,     5
    .equ PS_OWB_SHIFT,  8
    .equ PS_OWB_BITS,   4
    .equ PS_WOE_SHIFT,  18

    /* One past the last window handler's slot, from VECBASE. */
    .equ WINDOW_VECTORS_END, 0x180

    /* a4..a15 of the current window to or from 12 words of the frame's
     * register area, starting `off` bytes in. */
    .macro  SAVE12 off
    s32i    a4, a1, F_AR + \off + 0
    s32i    a5, a1, F_AR + \off + 4
    s32i    a6, a1, F_AR + \off + 8
    s32i    a7, a1, F_AR + \off + 12
    s32i    a8, a1, F_AR + \off + 16
    s32i    a9, a1, F_AR + \off + 20
    s32i    a10, a1, F_AR + \off + 24
    s32i    a11, a1, F_AR + \off + 28
    s32i    a12, a1, F_AR + \off + 32
    s32i    a13, a1, F_AR + \off + 36
    s32i    a14, a1, F_AR + \off + 40
    s32i    a15, a1, F_AR + \off + 44
    .endm
    .macro  LOAD12 off
    l32i    a4, a1, F_AR + \off + 0
    l32i    a5, a1, F_AR + \off + 4
    l32i    a6, a1, F_AR + \off + 8
    l32i    a7, a1, F_AR + \off + 12
    l32i    a8, a1, F_AR + \off + 16
    l32i    a9, a1, F_AR + \off + 20
    l32i    a10, a1, F_AR + \off + 24
    l32i    a11, a1, F_AR + \off + 28
    l32i    a12, a1, F_AR + \off + 32
    l32i    a13, a1, F_AR + \off + 36
    l32i    a14, a1, F_AR + \off + 40
    l32i    a15, a1, F_AR + \off + 44
    .endm

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
    /* The ROM arrives windowed (`callx8`, PS.WOE = 1). Turn window
     * exceptions off, then leave exactly one live window. Until PS is
     * written, an instruction naming a4 or above would raise an overflow if
     * the ROM's WINDOWSTART had a bit in the panes above its window, so this
     * names a2 alone, which no window check reads. Writing WINDOWBASE renames
     * every address register, so nothing is kept in one across it; nothing
     * needs to be, since this path never returns to the ROM. */
    movi    a2, PS_KERNEL
    wsr     a2, ps
    rsync
    movi    a2, 1
    wsr     a2, windowstart
    movi    a2, 0
    wsr     a2, windowbase
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
     * unlocked with its key for the one write and locked again after.
     *
     * No emulator case can hold these writes: with a key wrong, or the RTC
     * config address a word low, every case still passes, because the
     * emulator models TIMG0's config and not the RTC watchdog's or
     * `SWD_CONF`, and starts every watchdog off. A board is the only check. */
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
    .literal .Lvecbase_lit, _vector_base

    /* VECBASE is the table's first byte. The ISA wants it 1 KiB aligned. */
    .align  1024
    .global _vector_base
_vector_base:

    /* The window handlers. Each runs with PS.EXCM = 1, WINDOWBASE rotated to
     * the frame k being spilled or filled, and PS.OWB the window to return
     * to; `rfwo` and `rfwu` return there and re-execute the instruction that
     * raised the exception. In frame k's numbering, n its call size in panes,
     * a(4n + 1) is its callee's stack pointer, and k's a0..a3 live in the 16
     * bytes below that (the base save area). A call8 or call12 frame's a4..
     * live in the 16 or 32 bytes below its caller's stack pointer less 16,
     * and that pointer is the word at k's own stack pointer less 12: the
     * caller is always spilled before k, so its base save area holds it.
     * They store and load through the thread's own registers only, so a bad
     * stack is a fault inside a handler, which the double vector below
     * reports as the thread's. */
    .org    0x000
_WindowOverflow4:
    s32e    a0, a5, -16
    s32e    a1, a5, -12
    s32e    a2, a5, -8
    s32e    a3, a5, -4
    rfwo
    .org    0x040
_WindowUnderflow4:
    l32e    a0, a5, -16
    l32e    a1, a5, -12
    l32e    a2, a5, -8
    l32e    a3, a5, -4
    rfwu
    .org    0x080
_WindowOverflow8:
    s32e    a0, a9, -16
    l32e    a0, a1, -12             /* a0 = the caller's stack pointer */
    s32e    a1, a9, -12
    s32e    a2, a9, -8
    s32e    a3, a9, -4
    s32e    a4, a0, -32
    s32e    a5, a0, -28
    s32e    a6, a0, -24
    s32e    a7, a0, -20
    rfwo
    .org    0x0C0
_WindowUnderflow8:
    l32e    a1, a9, -12             /* k's stack pointer first: the caller's */
    l32e    a0, a9, -16             /* is found through it */
    l32e    a7, a1, -12
    l32e    a2, a9, -8
    l32e    a3, a9, -4
    l32e    a4, a7, -32
    l32e    a5, a7, -28
    l32e    a6, a7, -24
    l32e    a7, a7, -20             /* the carrier last */
    rfwu
    .org    0x100
_WindowOverflow12:
    s32e    a0, a13, -16
    l32e    a0, a1, -12
    s32e    a1, a13, -12
    s32e    a2, a13, -8
    s32e    a3, a13, -4
    s32e    a4, a0, -48
    s32e    a5, a0, -44
    s32e    a6, a0, -40
    s32e    a7, a0, -36
    s32e    a8, a0, -32
    s32e    a9, a0, -28
    s32e    a10, a0, -24
    s32e    a11, a0, -20
    rfwo
    .org    0x140
_WindowUnderflow12:
    l32e    a1, a13, -12
    l32e    a0, a13, -16
    l32e    a11, a1, -12
    l32e    a2, a13, -8
    l32e    a3, a13, -4
    l32e    a4, a11, -48
    l32e    a5, a11, -44
    l32e    a6, a11, -40
    l32e    a7, a11, -36
    l32e    a8, a11, -32
    l32e    a9, a11, -28
    l32e    a10, a11, -24
    l32e    a11, a11, -20
    rfwu
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
    wsr     a0, excsave1            /* Kernel: a trap taken with PS.UM = 0 */
    rsr     a0, exccause
    bnei    a0, EXCCAUSE_ALLOCA, 1f
    j       kernel_alloca
1:  movi    a2, 0x300
    j       vec_fatal
    .org    0x340
_UserExceptionVector:
    xsr     a1, excsave1            /* User: a1 = frame, EXCSAVE1 = user a1 */
    s32i    a0, a1, F_A0
    rsr     a0, exccause
    bnei    a0, EXCCAUSE_ALLOCA, 1f
    j       user_alloca
1:  j       trap_entry
    .org    0x3C0
    rsr     a0, depc                /* Double: a trap taken with PS.EXCM = 1 */
    j       double_entry
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

/* The double exception, DEPC in a0. Neither path resumes the context the
 * exception interrupted, so a0 and a2 are free, and none of its registers is
 * parked in a special register a later thread could read. A fault a window
 * handler took for a user thread (DEPC in the six window vectors, PS.UM = 1)
 * is that thread's own bad access to its stack: it goes through the user
 * vector as an ordinary user trap, with EXCCAUSE and EXCVADDR the access's
 * and EPC1 still the user instruction whose window exception it was, since a
 * double exception leaves EPC1 alone, and the kernel ends the thread's
 * process. EXCSAVE1 is a frame only while user context runs, which
 * PS.UM = 1 vouches for. Every other double exception is the kernel's and
 * stops the machine. */
double_entry:
    l32r    a2, .Lvecbase_lit
    sub     a0, a0, a2
    movi    a2, WINDOW_VECTORS_END
    bgeu    a0, a2, 1f
    rsr     a0, ps
    bbci    a0, PS_UM_BIT, 1f
    j       _UserExceptionVector
1:  movi    a2, 0x3C0
    j       vec_fatal

/* The Alloca exception in the kernel, which only windowed code reached
 * through `sos_windowed_call` raises. The faulting window's a0 is in
 * EXCSAVE1, which the kernel holds nothing in: the trap entry has stored the
 * user's a1 it carried, and every resume writes it again. */
kernel_alloca:
    rsr     a0, windowbase
    rotw    -1
    mov     a3, a4                  /* the faulting window's WINDOWBASE */
    rsr     a4, excsave1            /* and its a0 */
    j       alloca_fill

/* The Alloca exception from user mode: `movsp` found every pane of its
 * caller's frame spilled. a1 is the frame and the user's a0 is in it. The
 * frame's WINDOWBASE word, which the next trap rewrites, carries the
 * faulting window's WINDOWBASE across the rotation, and a0 the frame's
 * address; the user's a1 goes back where it was. */
user_alloca:
    rsr     a0, windowbase
    s32i    a0, a1, F_WINDOWBASE
    mov     a0, a1
    xsr     a1, excsave1            /* a1 = user a1, EXCSAVE1 = frame */
    rotw    -1
    l32i    a3, a4, F_WINDOWBASE
    l32i    a4, a4, F_A0            /* the faulting window's a0 back */
    /* Fall through. */

/* Fill the caller's frame as a window underflow would, then retry the
 * `movsp`, which finds its caller live. Entered rotated one pane back from
 * the faulting window, with that window's registers intact as a4.. and its
 * WINDOWBASE in a3: the pane rotated to is the caller's, which is spilled,
 * so a0..a3 are dead and free. PS.OWB is set to the faulting window, the
 * window rotated to the caller's base by its call size, read from the top
 * bits of the faulting window's a0 (now a4), and the underflow handler for
 * that size returns through `rfwu` to the `movsp`. A size of 0 is read as
 * call4. */
alloca_fill:
    rsr     a2, ps
    extui   a1, a2, PS_OWB_SHIFT, PS_OWB_BITS
    slli    a1, a1, PS_OWB_SHIFT
    xor     a2, a2, a1              /* PS.OWB cleared */
    slli    a3, a3, PS_OWB_SHIFT
    or      a2, a2, a3
    wsr     a2, ps
    rsync
    bbsi    a4, 31, 1f
    j       _WindowUnderflow4
1:  rotw    -1                      /* the faulting window's a0 is a8 now */
    bbsi    a8, 30, 2f
    j       _WindowUnderflow8
2:  rotw    -1
    j       _WindowUnderflow12

/* A syscall, a level-1 interrupt or a fault, from user mode. a1 is the
 * running thread's frame and the user's a0 is in it, except from the double
 * path, whose thread is never resumed. PS.EXCM is 1 for the whole save, so no
 * window exception is raised whatever window state the thread left. */
trap_entry:
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
    rsr     a2, windowbase
    s32i    a2, a1, F_WINDOWBASE
    rsr     a2, windowstart
    s32i    a2, a1, F_WINDOWSTART

    /* The zero-overhead loop is address-triggered: with LCOUNT non-zero,
     * kernel code reaching the user's LEND address (shared code in a flat
     * image) would branch to the user's LBEG. */
    movi    a2, 0
    wsr     a2, lcount

    /* The other 48 physical registers, panes WINDOWBASE + 4 to + 15, three
     * panes at a time. `rotw 3` makes a12..a15 the new a0..a3, so the frame's
     * address, copied into a13 (already saved), is a1 again after it. */
    mov     a13, a1
    rotw    3
    SAVE12  0
    mov     a13, a1
    rotw    3
    SAVE12  48
    mov     a13, a1
    rotw    3
    SAVE12  96
    mov     a13, a1
    rotw    3
    SAVE12  144

    /* Become the kernel, on this window (WINDOWBASE + 12) with the thread's
     * WINDOWSTART left in place: with PS.WOE = 0 neither is read. The context
     * is saved, so EXCM can drop: a fault from here on arrives at the kernel
     * vector with its own EPC1, PS.WOE = 0 keeps every window exception off,
     * and INTLEVEL 15 keeps every interrupt pending until user mode runs
     * again. */
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
 * so writing it keeps interrupts masked and window exceptions off until
 * `rfe`, which clears EXCM and resumes at the frame's PC with its UM,
 * INTLEVEL and window fields. Every one of the 64 physical registers is
 * loaded from the frame, so nothing of the kernel's or another thread's
 * stays readable. Re-arming EXCSAVE1 with the frame makes the next trap save
 * into the thread that is actually running. */
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
    /* LCOUNT once the frame's PS and its EXCM are in place: the loop-back
     * at LEND is taken only with EXCM clear, so from here to `rfe` a live
     * count cannot send this code to the user's LBEG, which the entry's own
     * zeroing guards against on the way in. */
    l32i    a2, a1, F_LCOUNT
    wsr     a2, lcount
    isync
    l32i    a2, a1, F_WINDOWSTART
    wsr     a2, windowstart
    /* To the frame's WINDOWBASE + 12, the window whose a4..a15 are its top
     * three panes; WINDOWBASE is four bits wide, so the write keeps the sum
     * mod 16. Writing WINDOWBASE renames every register, so the frame's
     * address rides across it in EXCSAVE1, where it stays. */
    wsr     a1, excsave1
    l32i    a2, a1, F_WINDOWBASE
    addi    a2, a2, 12
    wsr     a2, windowbase
    rsync
    rsr     a1, excsave1
    /* Top down, three panes at a time: after each `rotw -3` the frame's
     * address is a13 of the group about to be loaded, so it moves to a1 first,
     * a register of the next group down or of the frame's own window. */
    LOAD12  144
    rotw    -3
    mov     a1, a13
    LOAD12  96
    rotw    -3
    mov     a1, a13
    LOAD12  48
    rotw    -3
    mov     a1, a13
    LOAD12  0
    rotw    -3
    mov     a1, a13
    /* The frame's own window. */
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
    l32i    a1, a1, F_A1            /* user a1 last: it retires the frame */
    rfe

/* ---- calling a windowed function from the kernel ------------------------- */

/* u32 sos_windowed_call(u32 fn, u32 a, u32 b, u32 c, u32 d, u32 e, u32 f)
 *
 * A call0 function that calls the windowed function `fn` with six arguments
 * and returns its result: how the call0 kernel reaches a function in the
 * mask ROM. The six are every argument register a `callx8` passes; `f` is
 * the call0 caller's seventh argument word, on its stack. A callee reads
 * only the arguments it takes, so a caller may pass fewer. It sits in IRAM,
 * so a caller that must not fetch through the flash cache while it runs (the
 * PLL's bring-up below) can call it. It is refused once the loader has begun
 * (`sos_windowed_call_close`): the ROM's functions read tables in the ROM's
 * RAM, which a process may be given, so after that the call stops the
 * machine through `sos_windowed_call_refused` (`lib.saw`) before `fn` runs.
 *
 * The kernel runs with PS.WOE = 0 and whatever WINDOWSTART the last trap
 * left. For the call this frame is the only live window (WINDOWSTART = 1 <<
 * WINDOWBASE), so the callee's first reference spills no frame that does not
 * exist, and PS.WOE = 1 with EXCM = 0, so `entry` is legal; INTLEVEL stays
 * 15, so no interrupt is taken. Both are put back as they were afterwards.
 * `callx8` makes the callee's window this frame's a8..a23, so a12..a15,
 * which call0 keeps, are saved here; a0..a7 are this frame's own, which the
 * windowed callee keeps, its return address among them. The 48 registers
 * outside the kernel's window are the callee's to clobber, and the next
 * resume reloads all 64.
 *
 * This frame is a call8 window to the callee, so it can be spilled and
 * filled like any other if the callee nests deep enough: the word at a1 - 12,
 * in the 16 bytes below this frame that the windowed ABI keeps for its
 * caller's spill, is the stack-pointer slot its own spill reads to find its
 * extra save area, and names this frame's top, so that area is bytes 32..47
 * of its 64. A fault in
 * such a spill or fill is the kernel's (the double vector's PS.UM test); an
 * Alloca exception under it is handled at the kernel vector.
 *
 * `retw` takes the top two bits of its return address from its own PC, so
 * this function must sit where those bits are 01, as IRAM does.
 *
 * Its section is not kept: `esp32s3.ld` places it in IRAM behind the trap
 * path, and an image that never calls the ROM leaves it out. */
    .section .iram.text.windowed_call, "ax"
    .literal .Lwindowed_call_closed, sos_windowed_call_closed
    .literal .Lwindowed_call_refused, sos_windowed_call_refused
    .align  4
    .global sos_windowed_call
    .type   sos_windowed_call, @function
sos_windowed_call:
    l32r    a8, .Lwindowed_call_closed
    l32i    a8, a8, 0
    bnez    a8, 1f
    l32i    a9, a1, 0               /* f */
    addi    a1, a1, -64
    s32i    a12, a1, 4
    s32i    a13, a1, 8
    s32i    a14, a1, 12
    s32i    a15, a1, 16
    rsr     a8, ps
    s32i    a8, a1, 20
    rsr     a8, windowstart
    s32i    a8, a1, 24
    addi    a8, a1, 64
    addi    a10, a1, -16
    s32i    a8, a10, 4
    rsr     a8, windowbase
    ssl     a8
    movi    a10, 1
    sll     a10, a10
    wsr     a10, windowstart
    movi    a10, 1
    slli    a10, a10, PS_WOE_SHIFT
    addi    a10, a10, PS_KERNEL
    wsr     a10, ps
    rsync
    mov     a10, a3
    mov     a11, a4
    mov     a12, a5
    mov     a13, a6
    mov     a14, a7
    mov     a15, a9
    callx8  a2
    mov     a2, a10
    l32i    a7, a1, 20
    wsr     a7, ps
    rsync
    l32i    a7, a1, 24
    wsr     a7, windowstart
    rsync
    l32i    a12, a1, 4
    l32i    a13, a1, 8
    l32i    a14, a1, 12
    l32i    a15, a1, 16
    addi    a1, a1, 64
    ret
1:  l32r    a0, .Lwindowed_call_refused
    callx0  a0                      /* sos_windowed_call_refused(fn): stops */

/* void sos_windowed_call_close(void): from here on `sos_windowed_call`
 * refuses. `lib.saw`'s `payload_start`, the loader's first read of the root
 * image, calls it. Any non-zero word closes the stub; the word's own address
 * is one. */
    .section .text.sos_windowed_call_close, "ax"
    .literal .Lwindowed_call_closed2, sos_windowed_call_closed
    .align  4
    .global sos_windowed_call_close
    .type   sos_windowed_call_close, @function
sos_windowed_call_close:
    l32r    a2, .Lwindowed_call_closed2
    s32i    a2, a2, 0
    ret

    .section .bss.sos_windowed_call_closed, "aw", @nobits
    .align  4
sos_windowed_call_closed:
    .space  4

/* ---- the clock store (IRAM) ---------------------------------------------- */

/* void sos_clock_store(u32 addr, u32 value)
 *
 * Store `value` at the clock register `addr`, read it back so the store has
 * reached the register, and return. It runs from IRAM, so no instruction is
 * fetched through the flash cache while the system clock, and with it the
 * memory-SPI clock, moves; the caller resumes from flash at the new clock.
 *
 * Its section is not kept: `esp32s3.ld` places it in IRAM behind the trap
 * path, and an image whose clock key writes nothing does not reference it, so
 * the link drops it and the image is the bytes it would be without it. */
    .section .iram.text.clock, "ax"
    .align  4
    .global sos_clock_store
sos_clock_store:
    memw
    s32i    a3, a2, 0
    memw
    l32i    a3, a2, 0
    memw
    ret

/* ---- the BBPLL's bring-up (IRAM) ----------------------------------------- */

/* u32 sos_pll_bringup(void)
 *
 * Power the BBPLL and set it to 480 MHz from the 40 MHz crystal, as ESP-IDF
 * v5.4.1 does before it moves the CPU onto the PLL: `rtc_clk_bbpll_enable`
 * and `rtc_clk_bbpll_configure(40, 480)` (esp_hw_support/port/esp32s3/
 * rtc_clk.c), with the analog bus enabled as `rtc_clk_init` does
 * (rtc_clk_init.c), though after P1 where ESP-IDF makes it before; both
 * precede the bus's first use. Each step's register and value, and the source
 * each is read from, are in ../ABI.md §14b (P1 to P7, R3). The CPU stays on
 * its clock; the caller makes the switch. Answers PLL_CAL_DONE, or
 * PLL_CAL_TIMEOUT when the BBPLL has not reported its calibration done within
 * PLL_CAL_BOUND cycles, after which nothing may switch to it.
 *
 * It runs from IRAM and calls only the mask ROM, through the stub, which is
 * IRAM too, so nothing is fetched through the flash cache while the PLL
 * moves: whether the memory-SPI core clock is drawn from the PLL while the
 * CPU is on the crystal is not in the sources, and ESP-IDF keeps the code
 * that powers the BBPLL down and up again in IRAM "because disabling BBPLL
 * may influence the cache" (esp_system/port/soc/esp32s3/clk.c,
 * `recalib_bbpll`). Its data is the kernel stack and the ROM's phy table,
 * neither in flash.
 *
 * Every register written is volatile: a power cycle restores it (ABI.md
 * §14d says what an EN-pin reset has been seen to restore). The one RTC
 * register is RTC_CNTL_OPTIONS0, whose three power-down bits it clears.
 *
 * Its section is not kept, as the clock store's is not. */
    .equ RTC_CNTL_OPTIONS0,     0x60008000
    /* BB_I2C_FORCE_PD [6], BBPLL_I2C_FORCE_PD [8], BBPLL_FORCE_PD [10]. */
    .equ BBPLL_FORCE_PD_BITS,   0x540
    .equ I2C_MST_ANA_CONF0,     0x6000E040
    .equ BBPLL_STOP_FORCE_HIGH, 0x4         /* [2] */
    .equ BBPLL_STOP_FORCE_LOW,  0x8         /* [3] */
    .equ BBPLL_CAL_DONE_BIT,    24
    /* ANA_CONFIG [17:8], each bit holding one analog block's bus off;
     * [17] is the BBPLL's. The ROM's register read stores the whole word
     * (ABI.md §14b). */
    .equ ANA_CONFIG,            0x6000E044
    .equ ANA_CONFIG_ALL,        0x3FF
    .equ ANA_CONFIG_SHIFT,      8
    .equ ANA_CONFIG_BBPLL_BIT,  17
    /* SYSTEM_CPU_PER_CONF: PLL_FREQ_SEL [2], 1 for 480 MHz. */
    .equ CPU_PER_CONF,          0x600C0010
    .equ PLL_FREQ_SEL_480,      0x4
    /* The ROM's analog-bus writers (esp32s3.rom.ld), windowed:
     * rom_i2c_writeReg(block, host, reg, data) and
     * rom_i2c_writeReg_Mask(block, host, reg, msb, lsb, data). */
    .equ ROM_I2C_WRITE,         0x40005D60
    .equ ROM_I2C_WRITE_MASK,    0x40005D6C
    /* The BBPLL's block on the analog bus, its host, and its registers
     * (soc/esp32s3/include/soc/regi2c_bbpll.h). */
    .equ I2C_BBPLL,             0x66
    .equ I2C_BBPLL_HOSTID,      1
    .equ BBPLL_OC_REF_DIV,      2
    .equ BBPLL_OC_DIV_7_0,      3
    .equ BBPLL_MODE_HF,         4
    .equ BBPLL_OC_DR,           5           /* OC_DR1 [2:0], OC_DR3 [6:4] */
    .equ BBPLL_OC_DCUR,         6
    .equ BBPLL_OC_VCO_DBIAS,    9           /* [1:0] */
    /* How long to wait for the calibration: ESP-IDF waits without a bound.
     * The CPU is on the crystal, at 40 MHz or less, so this is at least
     * 100 ms. */
    .equ PLL_CAL_BOUND,         4000000
    /* ESP-IDF's `esp_rom_delay_us(10)` after the calibration: at 40 MHz or
     * less, 400 cycles are at least 10 us. */
    .equ PLL_SETTLE_CYCLES,     400
    .equ PLL_CAL_DONE,          0
    .equ PLL_CAL_TIMEOUT,       1

    /* Clear, or set, the bits of register `reg` that `mask` holds. */
    .macro  REG_CLEAR reg, mask
    memw
    l32i    a9, \reg, 0
    or      a9, a9, \mask
    xor     a9, a9, \mask
    memw
    s32i    a9, \reg, 0
    memw
    .endm
    .macro  REG_SET reg, mask
    memw
    l32i    a9, \reg, 0
    or      a9, a9, \mask
    memw
    s32i    a9, \reg, 0
    memw
    .endm

    /* One BBPLL register through the ROM, whole or a field of it. */
    .macro  BBPLL_WRITE reg, value
    l32r    a2, .Lpll_rom_write
    movi    a3, I2C_BBPLL
    movi    a4, I2C_BBPLL_HOSTID
    movi    a5, \reg
    movi    a6, \value
    l32r    a0, .Lpll_windowed_call
    callx0  a0
    .endm
    .macro  BBPLL_WRITE_MASK reg, msb, lsb, value
    l32r    a2, .Lpll_rom_write_mask
    movi    a3, I2C_BBPLL
    movi    a4, I2C_BBPLL_HOSTID
    movi    a5, \reg
    movi    a6, \msb
    movi    a7, \lsb
    movi    a8, \value
    s32i    a8, a1, 0               /* the seventh argument word */
    l32r    a0, .Lpll_windowed_call
    callx0  a0
    .endm

    .section .iram.text.pll_bringup, "ax"
    .literal .Lpll_options0, RTC_CNTL_OPTIONS0
    .literal .Lpll_ana_config, ANA_CONFIG
    .literal .Lpll_ana_conf0, I2C_MST_ANA_CONF0
    .literal .Lpll_cpu_per_conf, CPU_PER_CONF
    .literal .Lpll_rom_write, ROM_I2C_WRITE
    .literal .Lpll_rom_write_mask, ROM_I2C_WRITE_MASK
    .literal .Lpll_windowed_call, sos_windowed_call
    .literal .Lpll_cal_bound, PLL_CAL_BOUND
    .align  4
    .global sos_pll_bringup
    .type   sos_pll_bringup, @function
sos_pll_bringup:
    addi    a1, a1, -32
    s32i    a0, a1, 16
    s32i    a12, a1, 20
    s32i    a13, a1, 24

    /* P1, `clk_ll_bbpll_enable`: the BBPLL and its analog bus powered. */
    l32r    a12, .Lpll_options0
    movi    a10, BBPLL_FORCE_PD_BITS
    REG_CLEAR a12, a10

    /* R3, `regi2c_ctrl_ll_i2c_reset` then `regi2c_ctrl_ll_i2c_bbpll_enable`
     * (hal/esp32s3/include/hal/regi2c_ctrl_ll.h): the buses of the blocks
     * in [17:8] held off, then the BBPLL's let on. ESP-IDF makes these
     * before P1. */
    l32r    a12, .Lpll_ana_config
    movi    a10, ANA_CONFIG_ALL
    slli    a10, a10, ANA_CONFIG_SHIFT
    REG_SET a12, a10
    movi    a10, 1
    slli    a10, a10, ANA_CONFIG_BBPLL_BIT
    REG_CLEAR a12, a10

    /* P2, `clk_ll_bbpll_set_freq_mhz(480)`. */
    l32r    a12, .Lpll_cpu_per_conf
    movi    a10, PLL_FREQ_SEL_480
    REG_SET a12, a10

    /* P3, `regi2c_ctrl_ll_bbpll_calibration_start`. */
    l32r    a12, .Lpll_ana_conf0
    movi    a10, BBPLL_STOP_FORCE_HIGH
    REG_CLEAR a12, a10
    movi    a10, BBPLL_STOP_FORCE_LOW
    REG_SET a12, a10

    /* P4, `clk_ll_bbpll_set_config(480, 40)`, in its order: MODE_HF 0x6B;
     * OC_REF_DIV (DCHGP 5 << 4) | DIV_REF 0; OC_DIV_7_0 8; OC_DR1 0; OC_DR3
     * 0; OC_DCUR (DLREF_SEL 1 << 6) | (DHREF_SEL 3 << 4) | DCUR 3;
     * OC_VCO_DBIAS 3. */
    BBPLL_WRITE BBPLL_MODE_HF, 0x6B
    BBPLL_WRITE BBPLL_OC_REF_DIV, 0x50
    BBPLL_WRITE BBPLL_OC_DIV_7_0, 8
    BBPLL_WRITE_MASK BBPLL_OC_DR, 2, 0, 0
    BBPLL_WRITE_MASK BBPLL_OC_DR, 6, 4, 0
    BBPLL_WRITE BBPLL_OC_DCUR, 0x73
    BBPLL_WRITE_MASK BBPLL_OC_VCO_DBIAS, 1, 0, 3

    /* P5, `regi2c_ctrl_ll_bbpll_calibration_is_done`, polled. */
    l32r    a12, .Lpll_ana_conf0
    rsr     a13, ccount
1:  memw
    l32i    a9, a12, 0
    bbsi    a9, BBPLL_CAL_DONE_BIT, 2f
    rsr     a9, ccount
    sub     a9, a9, a13
    l32r    a10, .Lpll_cal_bound
    bltu    a9, a10, 1b
    movi    a13, PLL_CAL_TIMEOUT
    j       4f

    /* P6, the settling delay. */
2:  rsr     a13, ccount
3:  rsr     a9, ccount
    sub     a9, a9, a13
    movi    a10, PLL_SETTLE_CYCLES
    bltu    a9, a10, 3b
    movi    a13, PLL_CAL_DONE

    /* P7, `regi2c_ctrl_ll_bbpll_calibration_stop`, made on either path so
     * the analog master is left as ESP-IDF leaves it. */
4:  movi    a10, BBPLL_STOP_FORCE_LOW
    REG_CLEAR a12, a10
    movi    a10, BBPLL_STOP_FORCE_HIGH
    REG_SET a12, a10

    mov     a2, a13
    l32i    a0, a1, 16
    l32i    a12, a1, 20
    l32i    a13, a1, 24
    addi    a1, a1, 32
    ret
