/* Bus probe leaves: accesses that report a refusal instead of stopping the
 * machine, on Xtensa (ESP32-S3), call0.
 *
 * `tests/bus_probe.saw` makes each access through one of the leaves below and
 * prints what it gave. A refused access raises an exception in kernel mode,
 * which the shipping table sends to `kernel_fault` and a halt. This file
 * brings a second vector table that only this probe's image links, and
 * `probe_install` points VECBASE at it for the probe's steps; the shipping
 * `boot.S` and `sink.c` are unchanged, so no other image has a path that
 * resumes after a kernel fault.
 *
 * The recovery, exactly:
 *   - Every access leaf first ARMs: it records its caller's return address
 *     (a0) and stack pointer (a1) in `probe_state` and sets `S_ARMED`. It
 *     then makes its one access and DISARMs, returning ACCESS_OK with what it
 *     read in `S_VALUE`.
 *   - A trap taken in kernel mode arrives at this table's kernel vector with
 *     PS.EXCM = 1. If `S_ARMED` is clear, nothing is resumed: the trap is
 *     reported through `kernel_fault` exactly as the shipping vector does.
 *     If it is set, the vector records EXCCAUSE and EXCVADDR, clears
 *     `S_ARMED`, restores a0 and a1 to the caller's, puts ACCESS_REFUSED in
 *     a2, writes the caller's return address into EPC1 and executes `rfe`.
 *   - `rfe` clears PS.EXCM and changes nothing else in PS, so the caller
 *     resumes with the PS it called with: UM = 0, EXCM = 0, INTLEVEL 15. To
 *     the caller the leaf returned ACCESS_REFUSED. Only a2..a11 differ from a
 *     normal return, and call0 lets a callee clobber those (the HAL's
 *     `ABI.md` §7e); a12..a15 are never touched on either path. The trap
 *     arrives before the faulting instruction retires, so a refused store
 *     has stored nothing and a refused call has run nothing.
 *   - Nothing outlives the resume: the vector disarms before `rfe`, EXCM is
 *     clear again, and the next leaf arms afresh, so a second refusal is
 *     handled exactly like the first.
 *   - The probe removes this table (`probe_remove`) before it enters user
 *     mode, so a process's traps reach the shipping user vector.
 *
 * The table must sit in IRAM, as the shipping one does: it goes in
 * `.text.vectors`, which `esp32s3.ld` places in the IRAM `boot.S` copies at
 * boot, and its literals in `.literal.vectors` ahead of it. The leaves run in
 * place from flash like the rest of the kernel.
 *
 * The `.payload` section is code this probe copies into RAM: a routine it
 * calls in kernel mode and a user-mode program. It is the image's payload, so
 * `hal.payload_start()` names its first byte in the flash instruction window.
 */

    .equ ACCESS_OK,         0
    .equ ACCESS_REFUSED,    1

    /* `probe_state`, one word each. */
    .equ S_ARMED,           0       /* non-zero while a leaf's access runs */
    .equ S_A0,              4       /* the leaf's caller: return address */
    .equ S_A1,              8       /* the leaf's caller: stack pointer */
    .equ S_VALUE,           12      /* what the last allowed access gave */
    .equ S_CAUSE,           16      /* EXCCAUSE of the last refusal */
    .equ S_VADDR,           20      /* EXCVADDR of the last refusal */
    .equ S_VECBASE,         24      /* the table `probe_install` replaced */
    .equ S_BYTES,           28

    /* The routine's result is its argument plus this. */
    .equ ROUTINE_ADDEND,    42

    /* System ops, as `payload_ok.S` makes them (spec §5.7). */
    .equ SYSTEM_HANDLE,     1
    .equ OP_DEBUG_PRINT,    0
    .equ OP_SHUTDOWN,       1

    /* The byte the user-mode program stores and reads back. */
    .equ USER_BYTE,         'K'

    .section .bss.probe, "aw", %nobits
    .balign 4
probe_state:
    .space  S_BYTES

/* ---- the probe's vector table (IRAM) ------------------------------------ */

    .section .text.vectors, "ax"
    .literal .Lv_state, probe_state
    .literal .Lv_kstack, _stack_top
    .literal .Lv_fault, kernel_fault

    .align  1024
    .global probe_vector_base
probe_vector_base:
    .org    0x000
    movi    a2, 0x000               /* WindowOverflow4 */
    j       probe_fatal
    .org    0x040
    movi    a2, 0x040               /* WindowUnderflow4 */
    j       probe_fatal
    .org    0x080
    movi    a2, 0x080               /* WindowOverflow8 */
    j       probe_fatal
    .org    0x0C0
    movi    a2, 0x0C0               /* WindowUnderflow8 */
    j       probe_fatal
    .org    0x100
    movi    a2, 0x100               /* WindowOverflow12 */
    j       probe_fatal
    .org    0x140
    movi    a2, 0x140               /* WindowUnderflow12 */
    j       probe_fatal
    .org    0x180
    movi    a2, 0x180               /* Level 2 */
    j       probe_fatal
    .org    0x1C0
    movi    a2, 0x1C0               /* Level 3 */
    j       probe_fatal
    .org    0x200
    movi    a2, 0x200               /* Level 4 */
    j       probe_fatal
    .org    0x240
    movi    a2, 0x240               /* Level 5 */
    j       probe_fatal
    .org    0x280
    movi    a2, 0x280               /* Debug */
    j       probe_fatal
    .org    0x2C0
    movi    a2, 0x2C0               /* NMI */
    j       probe_fatal
    .org    0x300
    j       probe_kernel_exception  /* Kernel: a trap taken with PS.UM = 0 */
    .org    0x340
    movi    a2, 0x340               /* User: the probe never runs user code */
    j       probe_fatal             /* with this table installed */
    .org    0x3C0
    movi    a2, 0x3C0               /* Double: a trap taken with PS.EXCM = 1 */
    j       probe_fatal
    .org    0x400

/* A trap in kernel mode. Resumed only when a leaf is armed; see the header. */
probe_kernel_exception:
    l32r    a2, .Lv_state
    l32i    a3, a2, S_ARMED
    beqz    a3, 1f
    rsr     a3, exccause
    s32i    a3, a2, S_CAUSE
    rsr     a3, excvaddr
    s32i    a3, a2, S_VADDR
    movi    a3, 0
    s32i    a3, a2, S_ARMED
    memw
    l32i    a0, a2, S_A0
    l32i    a1, a2, S_A1
    wsr     a0, epc1
    rsync
    movi    a2, ACCESS_REFUSED
    rfe
1:  movi    a2, 0x300
    /* Fall through. */

/* Any other trap: report it and stop, as `boot.S`'s `vec_fatal` does. */
probe_fatal:
    rsr     a3, exccause
    rsr     a4, epc1
    rsr     a5, excvaddr
    rsr     a6, depc
    l32r    a1, .Lv_kstack
    l32r    a0, .Lv_fault
    callx0  a0
1:  j       1b

/* ---- the leaves (flash) -------------------------------------------------- */

    .section .text.probe, "ax"
    .literal .Lstate, probe_state
    .literal .Lvectors, probe_vector_base
    .literal .Luser_literal, probe_user_literal_slot
    .literal .Luser_entry, probe_user_entry_point

/* Record the caller and mark the access as one to resume. Uses a8, a9. */
    .macro ARM
    l32r    a8, .Lstate
    s32i    a0, a8, S_A0
    s32i    a1, a8, S_A1
    movi    a9, 1
    s32i    a9, a8, S_ARMED
    memw
    .endm

/* The access was allowed: keep `\value` and return ACCESS_OK. a8 still holds
 * `probe_state` from ARM. */
    .macro DONE value
    s32i    \value, a8, S_VALUE
    movi    a9, 0
    s32i    a9, a8, S_ARMED
    memw
    movi    a2, ACCESS_OK
    ret
    .endm

/* void probe_install(void): point VECBASE at this file's table. */
    .balign 4
    .global probe_install
probe_install:
    l32r    a8, .Lstate
    rsr     a9, vecbase
    s32i    a9, a8, S_VECBASE
    l32r    a9, .Lvectors
    wsr     a9, vecbase
    rsync
    ret

/* void probe_remove(void): give VECBASE back to the table it replaced. */
    .balign 4
    .global probe_remove
probe_remove:
    l32r    a8, .Lstate
    l32i    a9, a8, S_VECBASE
    wsr     a9, vecbase
    rsync
    ret

/* u32 probe_load8(u32 addr), probe_load16, probe_load32: one load. */
    .balign 4
    .global probe_load8
probe_load8:
    ARM
    l8ui    a3, a2, 0
    DONE    a3

    .balign 4
    .global probe_load16
probe_load16:
    ARM
    l16ui   a3, a2, 0
    DONE    a3

    .balign 4
    .global probe_load32
probe_load32:
    ARM
    l32i    a3, a2, 0
    DONE    a3

/* u32 probe_store8(u32 addr, u32 v), probe_store16, probe_store32: one
 * store, then `memw` so the store has left the core before the leaf reports
 * it allowed. */
    .balign 4
    .global probe_store8
probe_store8:
    ARM
    s8i     a3, a2, 0
    memw
    DONE    a3

    .balign 4
    .global probe_store16
probe_store16:
    ARM
    s16i    a3, a2, 0
    memw
    DONE    a3

    .balign 4
    .global probe_store32
probe_store32:
    ARM
    s32i    a3, a2, 0
    memw
    DONE    a3

/* u32 probe_s32c1i(u32 addr, u32 expected, u32 v): store `v` if the word is
 * `expected`; the value kept is the word as it was. */
    .balign 4
    .global probe_s32c1i
probe_s32c1i:
    ARM
    wsr     a3, scompare1
    s32c1i  a4, a2, 0
    memw
    DONE    a4

/* u32 probe_call(u32 addr, u32 arg): call the code at `addr` with `arg`; the
 * value kept is what it returned. `isync` first, so instructions stored as
 * data since the last one are what is fetched. */
    .balign 4
    .global probe_call
probe_call:
    ARM
    isync
    mov     a10, a2
    mov     a2, a3
    callx0  a10
    l32r    a8, .Lstate
    l32i    a0, a8, S_A0
    DONE    a2

/* What the last allowed access gave, and what the last refusal was. */
    .balign 4
    .global probe_value
probe_value:
    l32r    a8, .Lstate
    l32i    a2, a8, S_VALUE
    ret

    .balign 4
    .global probe_cause
probe_cause:
    l32r    a8, .Lstate
    l32i    a2, a8, S_CAUSE
    ret

    .balign 4
    .global probe_vaddr
probe_vaddr:
    l32r    a8, .Lstate
    l32i    a2, a8, S_VADDR
    ret

/* The processor state the probe reports or checks. */
    .balign 4
    .global probe_ps
probe_ps:
    rsr     a2, ps
    ret

    .balign 4
    .global probe_atomctl
probe_atomctl:
    rsr     a2, atomctl
    ret

/* Where the user-mode program's literal and entry sit in the payload, as
 * flash instruction-window addresses. */
    .balign 4
    .global probe_user_literal
probe_user_literal:
    l32r    a2, .Luser_literal
    ret

    .balign 4
    .global probe_user_entry
probe_user_entry:
    l32r    a2, .Luser_entry
    ret

/* ---- the payload: code the probe copies into RAM ------------------------- */

    .section .payload, "ax"
    .balign 4

/* u32 routine(u32 x): x + ROUTINE_ADDEND. Position-independent, touches no
 * memory, so a word-for-word copy anywhere runs the same. */
    .global probe_routine
probe_routine:
    addi    a2, a2, ROUTINE_ADDEND
    ret

/* The user-mode program. The probe copies the payload into process IRAM,
 * stores a DRAM address in the literal slot, and enters user mode at the
 * entry point with no stack. It loads the address with `l32r` (a load from
 * its own IRAM copy), stores USER_BYTE there and reads it back with byte
 * accesses, prints `SOS-U ` and the byte it read, then `SOS probe: done`,
 * and shuts the machine down, all through System ops. `l32r` reaches the slot
 * by a PC-relative offset, which a copy that keeps word alignment keeps. */
    .balign 4
    .global probe_user_literal_slot
probe_user_literal_slot:
    .word   0

    .macro PUTC c
    movi    a2, SYSTEM_HANDLE
    movi    a3, \c
    syscall
    .endm

    .global probe_user_entry_point
probe_user_entry_point:
    l32r    a4, probe_user_literal_slot
    movi    a5, USER_BYTE
    s8i     a5, a4, 0
    memw
    l8ui    a6, a4, 0
    movi    a8, OP_DEBUG_PRINT
    PUTC    'S'
    PUTC    'O'
    PUTC    'S'
    PUTC    '-'
    PUTC    'U'
    PUTC    0x20
    movi    a2, SYSTEM_HANDLE
    mov     a3, a6
    syscall
    PUTC    10
    PUTC    'S'
    PUTC    'O'
    PUTC    'S'
    PUTC    0x20
    PUTC    'p'
    PUTC    'r'
    PUTC    'o'
    PUTC    'b'
    PUTC    'e'
    PUTC    ':'
    PUTC    0x20
    PUTC    'd'
    PUTC    'o'
    PUTC    'n'
    PUTC    'e'
    PUTC    10
    movi    a8, OP_SHUTDOWN
    movi    a2, SYSTEM_HANDLE
    movi    a3, 0
    syscall
    /* `shutdown` does not return. If it ever did, spin here rather than run
     * off the end of the copy. */
1:  j       1b
    .balign 4
