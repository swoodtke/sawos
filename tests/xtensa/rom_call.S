/* What the `rom_*` kernels call through `sos_windowed_call`, and the call0
 * helpers around it, for the ESP32-S3.
 *
 * Windowed, reached by `callx8` from the stub:
 *
 * u32 wtest_deep(u32 depth, u32 seed): call8 recursion. Each frame holds
 * depth in a2, seed in a3 and seed + (depth << 8) + i in a4..a7, calls itself
 * on depth - 1, checks every value and the answer, seed + ((depth - 1) << 16),
 * after the call, and answers seed + (depth << 16), or 0x80000000 |
 * depth << 8 | i on a mismatch. Deep enough, it spills the stub's frame.
 *
 * u32 wtest_clobber(void): writes its a4..a7, which are the stub's a12..a15,
 * and answers 0.
 *
 * u32 wtest_args(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f): a ^ b << 4 ^
 * c << 8 ^ d << 12 ^ e << 16 ^ f << 20, so each of the six arguments shows.
 *
 * u32 wtest_ps_inside(void): PS as the callee runs.
 *
 * u32 wtest_alloca12(u32 seed): holds seed + i in a3..a11, calls a frame
 * with call12 that spills every older frame, the stub's included, and then
 * moves its stack pointer with `movsp`: the Alloca exception with PS.UM = 0.
 * Answers seed when every register came back, or 0x80000000 | i for the
 * first that did not.
 *
 * void wtest_spill_fault(u32 bad): makes `bad` its stack pointer and wraps
 * the register file, so the stub's frame is spilled below `bad`: a fault
 * inside a window handler with PS.UM = 0.
 *
 * Call0, called from Saw:
 *
 * u32 wtest_deep_addr(void), wtest_clobber_addr, wtest_args_addr,
 * wtest_ps_inside_addr,
 * wtest_alloca12_addr, wtest_spill_fault_addr: the windowed functions'
 * addresses.
 *
 * u32 wtest_rotated_call(u32 fn, u32 a): `sos_windowed_call(fn, a, 0, 0, 0)`
 * made from one pane further on, WINDOWBASE + 1, which is where a kernel
 * that has taken traps may stand, rather than the 0 the boot path leaves; it
 * keeps a0 and a12..a15 for its own caller across the rotation. The pane it
 * leaves is outside the stub's window, the callee's to clobber, so its stack
 * pointer waits in memory, not in a register there.
 *
 * u32 wtest_ps(void): PS.
 *
 * u32 wtest_hold(u32 fn): holds a value in each of a12..a15 across
 * `sos_windowed_call(fn, 0, 0, 0, 0)` and answers a bit per register that
 * came back changed, bit 0 for a12.
 *
 * void wtest_dirty_windowstart(void): sets every WINDOWSTART bit, as a
 * process's state can leave it in the kernel.
 * u32 wtest_windowstart(void): WINDOWSTART.
 *
 * u32 wtest_crc_input(void): the address of "123456789". */

/* ---- windowed ------------------------------------------------------------ */

    .section .text.wtest_deep, "ax"
    .align  4
    .type   wtest_deep, @function
wtest_deep:
    entry   a1, 32
    slli    a8, a2, 8
    add     a8, a8, a3
    addi    a4, a8, 4
    addi    a5, a8, 5
    addi    a6, a8, 6
    addi    a7, a8, 7
    beqz    a2, 2f
    addi    a10, a2, -1
    mov     a11, a3
    call8   wtest_deep
    addi    a8, a2, -1
    slli    a8, a8, 16
    add     a8, a8, a3
    movi    a11, 2
    bne     a10, a8, 3f
    slli    a8, a2, 8
    add     a8, a8, a3
    .irp    i, 4,5,6,7
    addi    a9, a8, \i
    movi    a11, \i
    bne     a\i, a9, 3f
    .endr
2:  slli    a2, a2, 16
    add     a2, a2, a3
    retw
3:  bbsi    a10, 31, 4f
    movi    a9, 1
    slli    a9, a9, 31
    slli    a8, a2, 8
    or      a9, a9, a8
    or      a2, a9, a11
    retw
4:  mov     a2, a10
    retw

    .section .text.wtest_clobber, "ax"
    .align  4
    .type   wtest_clobber, @function
wtest_clobber:
    entry   a1, 32
    movi    a4, -1
    movi    a5, -2
    movi    a6, -3
    movi    a7, -4
    movi    a2, 0
    retw

    .section .text.wtest_args, "ax"
    .align  4
    .type   wtest_args, @function
wtest_args:
    entry   a1, 32
    slli    a3, a3, 4
    slli    a4, a4, 8
    slli    a5, a5, 12
    slli    a6, a6, 16
    slli    a7, a7, 20
    xor     a2, a2, a3
    xor     a2, a2, a4
    xor     a2, a2, a5
    xor     a2, a2, a6
    xor     a2, a2, a7
    retw

    .section .text.wtest_ps_inside, "ax"
    .align  4
    .type   wtest_ps_inside, @function
wtest_ps_inside:
    entry   a1, 32
    rsr     a2, ps
    retw

    .section .text.wtest_wrap, "ax"
    .align  4
    .type   wtest_wrap, @function
wtest_wrap:
    entry   a1, 32
    beqz    a2, 1f
    addi    a10, a2, -1
    call8   wtest_wrap
1:  retw

    .section .text.wtest_mover, "ax"
    .align  4
    .type   wtest_mover, @function
wtest_mover:
    entry   a1, 32
    movi    a10, 20
    call8   wtest_wrap
    addi    a8, a1, -64
    movsp   a1, a8
    movi    a10, 20
    call8   wtest_wrap
    retw

    .section .text.wtest_alloca12, "ax"
    .align  4
    .type   wtest_alloca12, @function
wtest_alloca12:
    entry   a1, 48
    .irp    i, 3,4,5,6,7,8,9,10,11
    addi    a\i, a2, \i
    .endr
    call12  wtest_mover
    .irp    i, 3,4,5,6,7,8,9,10,11
    addi    a12, a2, \i
    movi    a13, \i
    bne     a\i, a12, 1f
    .endr
    retw
1:  movi    a12, 1
    slli    a12, a12, 31
    or      a2, a12, a13
    retw

    .section .text.wtest_spill_fault, "ax"
    .align  4
    .type   wtest_spill_fault, @function
wtest_spill_fault:
    entry   a1, 32
    mov     a1, a2
    movi    a10, 12
    call8   wtest_wrap
    retw

/* ---- call0 --------------------------------------------------------------- */

    .macro  ADDR name, target
    .section .text.\name, "ax"
    .literal .L\name\()_lit, \target
    .align  4
    .global \name
    .type   \name, @function
\name:
    l32r    a2, .L\name\()_lit
    ret
    .endm

    ADDR wtest_deep_addr, wtest_deep
    ADDR wtest_clobber_addr, wtest_clobber
    ADDR wtest_args_addr, wtest_args
    ADDR wtest_ps_inside_addr, wtest_ps_inside
    ADDR wtest_alloca12_addr, wtest_alloca12
    ADDR wtest_spill_fault_addr, wtest_spill_fault
    ADDR wtest_crc_input, .Lcrc_input

    .section .text.wtest_hold, "ax"
    .literal .Lwtest_hold_stub, sos_windowed_call
    .literal .Lwtest_hold_seed, 0x5EED1200
    .align  4
    .global wtest_hold
    .type   wtest_hold, @function
wtest_hold:
    addi    a1, a1, -32
    s32i    a0, a1, 0
    s32i    a12, a1, 4
    s32i    a13, a1, 8
    s32i    a14, a1, 12
    s32i    a15, a1, 16
    l32r    a12, .Lwtest_hold_seed
    addi    a13, a12, 1
    addi    a14, a12, 2
    addi    a15, a12, 3
    movi    a3, 0
    movi    a4, 0
    movi    a5, 0
    movi    a6, 0
    l32r    a0, .Lwtest_hold_stub
    callx0  a0
    movi    a2, 0
    l32r    a3, .Lwtest_hold_seed
    beq     a12, a3, 1f
    addi    a2, a2, 1
1:  addi    a3, a3, 1
    beq     a13, a3, 2f
    addi    a2, a2, 2
2:  addi    a3, a3, 1
    beq     a14, a3, 3f
    addi    a2, a2, 4
3:  addi    a3, a3, 1
    beq     a15, a3, 4f
    addi    a2, a2, 8
4:  l32i    a0, a1, 0
    l32i    a12, a1, 4
    l32i    a13, a1, 8
    l32i    a14, a1, 12
    l32i    a15, a1, 16
    addi    a1, a1, 32
    ret

    .section .text.wtest_rotated_call, "ax"
    .literal .Lwtest_rotated_stub, sos_windowed_call
    .literal .Lwtest_rotated_sp, wtest_rotated_sp
    .align  4
    .global wtest_rotated_call
    .type   wtest_rotated_call, @function
wtest_rotated_call:
    addi    a1, a1, -32
    s32i    a0, a1, 0
    s32i    a12, a1, 4
    s32i    a13, a1, 8
    s32i    a14, a1, 12
    s32i    a15, a1, 16
    l32r    a4, .Lwtest_rotated_sp
    s32i    a1, a4, 0
    mov     a5, a1                  /* a1, a2 and a3 after the rotation */
    mov     a6, a2
    mov     a7, a3
    rotw    1
    movi    a4, 0
    movi    a5, 0
    movi    a6, 0
    l32r    a0, .Lwtest_rotated_stub
    callx0  a0
    rotw    -1
    mov     a2, a6                  /* the answer, the rotated window's a2 */
    l32r    a1, .Lwtest_rotated_sp
    l32i    a1, a1, 0
    l32i    a0, a1, 0
    l32i    a12, a1, 4
    l32i    a13, a1, 8
    l32i    a14, a1, 12
    l32i    a15, a1, 16
    addi    a1, a1, 32
    ret

    .section .text.wtest_ps, "ax"
    .align  4
    .global wtest_ps
    .type   wtest_ps, @function
wtest_ps:
    rsr     a2, ps
    ret

    .section .text.wtest_dirty_windowstart, "ax"
    .align  4
    .global wtest_dirty_windowstart
    .type   wtest_dirty_windowstart, @function
wtest_dirty_windowstart:
    movi    a2, -1
    wsr     a2, windowstart
    rsync
    ret

    .section .text.wtest_windowstart, "ax"
    .align  4
    .global wtest_windowstart
    .type   wtest_windowstart, @function
wtest_windowstart:
    rsr     a2, windowstart
    ret

    .section .bss.wtest_rotated_sp, "aw", @nobits
    .align  4
wtest_rotated_sp:
    .space  4

    .section .rodata.wtest_crc_input, "a"
    .align  4
.Lcrc_input:
    .ascii  "123456789"
