/* The thread entries of `regs.c`, in assembly because each must run its
 * first instructions on registers no compiled prologue has touched. None
 * opens with `entry`, so each starts in the pane its thread starts in
 * (WINDOWBASE 0, WINDOWSTART naming that pane alone). WINDOWSTART stays so,
 * and no register any of them names lies in a pane above its window that has
 * a WINDOWSTART bit, so no window exception is raised: the fillers name only
 * a0..a3 away from pane 0, and the probe's windows start at panes 0, 4, 8
 * and 12. */

    .equ SYS_SHUTDOWN,       1
    .equ PROC_THREAD_SELF,   1
    .equ THREAD_START,       0
    .equ THREAD_JOIN,        1
    .equ THREAD_EXIT,        2

    /* Write base + 4 * k + r into register r of the k-th pane reached,
     * panes 1, ..., 15 and then 0 (k = 16), rotating one pane at a time and
     * carrying the next pane's value in SCOMPARE1. `base` is a literal. */
    .macro  FILL base
    l32r    a0, \base
    wsr     a0, scompare1
    .rept   16
    rotw    1
    rsr     a0, scompare1
    addi    a1, a0, 1
    addi    a2, a0, 2
    addi    a3, a0, 4
    wsr     a3, scompare1
    addi    a3, a0, 3
    .endr
    .endm

/* void fill_exit_entry(u32 unused): learns its own handle, fills every
 * register with 0xf0f10000 + 4 * k + r, and exits, so the trap entry saves
 * the pattern into its frame, which is then free for the next thread. */
    .section .text.fill_exit_entry, "ax"
    .literal .Lexit_pattern, 0xF0F10004
    .literal .Lproc, g_proc
    .literal .Lself, g_exiter
    .align  4
    .global fill_exit_entry
    .type   fill_exit_entry, @function
fill_exit_entry:
    l32r    a2, .Lproc
    l32i    a2, a2, 0
    movi    a3, 0
    movi    a4, 0
    movi    a5, 0
    movi    a8, PROC_THREAD_SELF
    syscall
    l32r    a2, .Lself
    s32i    a3, a2, 0
    FILL    .Lexit_pattern
    l32r    a2, .Lself
    l32i    a2, a2, 0
    movi    a3, 0
    movi    a8, THREAD_EXIT
    syscall
1:  j       1b

/* void fill_entry(u32 unused): fills every register with 0xf1f10000 + 4 * k
 * + r, then starts the probe and joins it, which hands the probe the
 * processor straight from this file, and shuts the machine down with the
 * probe's exit code. Before the probe runs, the syscalls overwrite a2 and a3
 * of pane 0 and a0 of pane 2, and the kernel runs on panes 12 to 15. */
    .section .text.fill_entry, "ax"
    .literal .Lpattern, 0xF1F10004
    .literal .Lprobe_handle, g_probe_handle
    .literal .Lboot, g_boot
    .align  4
    .global fill_entry
    .type   fill_entry, @function
fill_entry:
    FILL    .Lpattern
    l32r    a3, .Lprobe_handle
    l32i    a2, a3, 0
    movi    a3, 0
    movi    a8, THREAD_START
    syscall
    l32r    a3, .Lprobe_handle
    l32i    a2, a3, 0
    movi    a8, THREAD_JOIN
    syscall
    l32r    a2, .Lboot
    l32i    a2, a2, 0               /* a3 is the probe's exit code */
    movi    a8, SYS_SHUTDOWN
    syscall
1:  j       1b

/* void probe_entry(u32 arg): stores physical register 16 * p + r, for p the
 * pane group 0..3 and r 0..15, at `g_regs` + 4 * (16 * p + r), rotating four
 * panes at a time with SCOMPARE1 carrying first the window's own a0 and then
 * the buffer's address. Then it puts its stack pointer back and calls
 * `report` with call4. */
    .section .text.probe_entry, "ax"
    .literal .Lregs, g_regs
    .align  4
    .global probe_entry
    .type   probe_entry, @function

    .macro  STORE_WINDOW base
    s32i    a1, a0, \base + 4
    s32i    a2, a0, \base + 8
    s32i    a3, a0, \base + 12
    s32i    a4, a0, \base + 16
    s32i    a5, a0, \base + 20
    s32i    a6, a0, \base + 24
    s32i    a7, a0, \base + 28
    s32i    a8, a0, \base + 32
    s32i    a9, a0, \base + 36
    s32i    a10, a0, \base + 40
    s32i    a11, a0, \base + 44
    s32i    a12, a0, \base + 48
    s32i    a13, a0, \base + 52
    s32i    a14, a0, \base + 56
    s32i    a15, a0, \base + 60
    rsr     a1, scompare1
    s32i    a1, a0, \base
    wsr     a0, scompare1
    .endm

probe_entry:
    wsr     a0, scompare1
    l32r    a0, .Lregs
    STORE_WINDOW 0
    rotw    4
    xsr     a0, scompare1
    STORE_WINDOW 64
    rotw    4
    xsr     a0, scompare1
    STORE_WINDOW 128
    rotw    4
    xsr     a0, scompare1
    STORE_WINDOW 192
    rotw    4
    rsr     a0, scompare1
    l32i    a1, a0, 4
    call4   report
1:  j       1b
