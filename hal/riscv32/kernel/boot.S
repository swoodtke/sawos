/* SOS boot shim + trap entry (designs 112, 140) — riscv32, QEMU `virt`, M-mode.
 *
 * QEMU (`-M virt -bios none -kernel sos.elf`) resets into M-mode and jumps to
 * the ELF entry at RAM base 0x8000_0000, where `.text.boot` (this file) is
 * placed first by virt.ld. The shim sets up the stack, installs `trap_entry`,
 * zeroes .bss, and calls the Saw `kmain`.
 *
 * M1 adds the M/U split. `mscratch` is the MODE WITNESS: it holds 0 while the
 * kernel runs and `&_trapframe` while U-mode runs. `trap_entry` swaps it with
 * sp on entry, so a single `bnez` separates the two worlds:
 *
 *   nonzero -> the trap came from U-mode. sp now points at the trap frame;
 *              save the user context, run the Saw handler `ktrap`, restore,
 *              `mret` back.
 *   zero    -> the trap came from the kernel itself, i.e. a kernel bug. There
 *              is no context worth saving: FAIL the run through the SiFive
 *              finisher exactly as M0's stub did (never hang).
 *
 * Trap frame layout (32 words, 128 bytes): word `i` holds register `x<i>`,
 * and word 0 — the slot x0 would occupy, which never needs saving — holds
 * `mepc`. So a register's offset is just its number times four.
 *
 * This file plus the CSR/PMP helpers in rt.c ARE the riscv32 half of the HAL
 * that design 79 (M1b) extracts when arm64 EL1 parity lands; nothing above the
 * `ktrap` call is architecture-neutral.
 */

    .equ TF_SIZE,       128         /* 32 words */
    .equ SIFIVE_TEST,   0x00100000
    .equ FINISHER_FAIL, 0x3333
    .equ MSTATUS_MPP,   0x1800      /* bits 12:11 — previous privilege mode */
    .equ MSTATUS_MPIE,  0x80        /* bit 7 — previous interrupt enable */

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* Stack: the 64 KiB reservation below (in .bss), top-aligned. */
    la      sp, _stack_top

    /* We are the kernel: mode witness clear. */
    csrw    mscratch, zero

    /* Install the machine trap vector (direct mode: mtvec low bits = 0). */
    la      t0, trap_entry
    csrw    mtvec, t0

    /* Zero .bss [_bss_start, _bss_end) a word at a time. */
    la      t0, _bss_start
    la      t1, _bss_end
1:
    bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:
    /* Enter the Saw kernel. It exits via the SiFive finisher and does not
     * return; if it ever does, hang rather than execute past the image. */
    call    kmain
hang:
    wfi
    j       hang

/* ---- trap entry ---------------------------------------------------------- */

    .section .text.trap, "ax", @progbits
    .globl trap_entry
    .balign 4
trap_entry:
    /* sp <-> mscratch. sp now holds the mode witness; mscratch holds the sp
     * the trapped context was using. */
    csrrw   sp, mscratch, sp
    bnez    sp, .Lfrom_user

    /* Kernel-mode trap: undo the swap so sp is the kernel stack again, then
     * FAIL. (M0's discipline, unchanged: a kernel fault never hangs.) */
    csrrw   sp, mscratch, sp
    j       kernel_fault

.Lfrom_user:
    sw      x1,   1*4(sp)
    sw      x3,   3*4(sp)
    sw      x4,   4*4(sp)
    sw      x5,   5*4(sp)
    sw      x6,   6*4(sp)
    sw      x7,   7*4(sp)
    sw      x8,   8*4(sp)
    sw      x9,   9*4(sp)
    sw      x10, 10*4(sp)
    sw      x11, 11*4(sp)
    sw      x12, 12*4(sp)
    sw      x13, 13*4(sp)
    sw      x14, 14*4(sp)
    sw      x15, 15*4(sp)
    sw      x16, 16*4(sp)
    sw      x17, 17*4(sp)
    sw      x18, 18*4(sp)
    sw      x19, 19*4(sp)
    sw      x20, 20*4(sp)
    sw      x21, 21*4(sp)
    sw      x22, 22*4(sp)
    sw      x23, 23*4(sp)
    sw      x24, 24*4(sp)
    sw      x25, 25*4(sp)
    sw      x26, 26*4(sp)
    sw      x27, 27*4(sp)
    sw      x28, 28*4(sp)
    sw      x29, 29*4(sp)
    sw      x30, 30*4(sp)
    sw      x31, 31*4(sp)

    /* The user sp is parked in mscratch by the entry swap. Save it, then clear
     * the witness — from here on we are the kernel. */
    csrr    t0, mscratch
    sw      t0,   2*4(sp)
    csrw    mscratch, zero

    csrr    t0, mepc
    sw      t0,   0*4(sp)

    /* Run the handler on the KERNEL stack, keeping the frame pointer in s0
     * (callee-saved, so `ktrap` must give it back). */
    mv      s0, sp
    mv      a0, s0
    csrr    a1, mcause
    csrr    a2, mtval
    la      sp, _stack_top
    call    ktrap

    /* Resume whatever context the frame now describes. `ktrap` may have
     * advanced mepc past an `ecall` and written syscall results into a0/a1. */
    mv      sp, s0
    lw      t0,   0*4(sp)
    csrw    mepc, t0
    /* Going back to U-mode: re-arm the witness. */
    csrw    mscratch, sp

    lw      x1,   1*4(sp)
    lw      x3,   3*4(sp)
    lw      x4,   4*4(sp)
    lw      x5,   5*4(sp)
    lw      x6,   6*4(sp)
    lw      x7,   7*4(sp)
    lw      x8,   8*4(sp)
    lw      x9,   9*4(sp)
    lw      x10, 10*4(sp)
    lw      x11, 11*4(sp)
    lw      x12, 12*4(sp)
    lw      x13, 13*4(sp)
    lw      x14, 14*4(sp)
    lw      x15, 15*4(sp)
    lw      x16, 16*4(sp)
    lw      x17, 17*4(sp)
    lw      x18, 18*4(sp)
    lw      x19, 19*4(sp)
    lw      x20, 20*4(sp)
    lw      x21, 21*4(sp)
    lw      x22, 22*4(sp)
    lw      x23, 23*4(sp)
    lw      x24, 24*4(sp)
    lw      x25, 25*4(sp)
    lw      x26, 26*4(sp)
    lw      x27, 27*4(sp)
    lw      x28, 28*4(sp)
    lw      x29, 29*4(sp)
    lw      x30, 30*4(sp)
    lw      x31, 31*4(sp)
    lw      x2,   2*4(sp)           /* user sp LAST — it retires the frame ptr */
    mret

/* A trap the KERNEL took: write FINISHER_FAIL with mcause in the code bits, so
 * the emulator exits non-zero carrying the fault cause. */
    .globl kernel_fault
kernel_fault:
    csrr    t0, mcause
    slli    t0, t0, 16
    li      t1, FINISHER_FAIL
    or      t0, t0, t1
    li      t2, SIFIVE_TEST
    sw      t0, 0(t2)
3:
    wfi
    j       3b

/* ---- M -> U transition --------------------------------------------------- */

/* void sos_enter_user(u32 entry, u32 stack_top, u32 boot_handle) — never returns.
 *
 * Arms the mode witness, points mepc at the user entry, selects U-mode as the
 * `mret` target (MPP = 0) with interrupts left off (MPIE = 0 — M1 has no
 * interrupt sources), and hands the user a zeroed register file so nothing of
 * the kernel's leaks across the privilege boundary. RISC-V has ONE `sp` across
 * modes, so setting it here is what gives root its stack.
 *
 * The one register that is NOT zeroed is a0, which carries the boot handle set
 * (spec §12). In M1 that is a single handle — root's System handle — so it fits
 * in a register; when the set grows it becomes a structure the kernel places in
 * root's memory and a0 points at. Either way this is the handoff: root's entry
 * takes it as its first C-ABI argument, so a Saw `_start(system: UInt)` just
 * receives it. */
    .section .text.enter, "ax", @progbits
    .globl sos_enter_user
sos_enter_user:
    la      t0, _trapframe
    csrw    mscratch, t0
    csrw    mepc, a0
    li      t1, MSTATUS_MPP
    csrc    mstatus, t1
    li      t1, MSTATUS_MPIE
    csrc    mstatus, t1
    mv      sp, a1
    /* Park the boot handle where the zeroing loop will not reach it. */
    mv      t2, a2

    li      x1, 0
    li      x3, 0
    li      x4, 0
    li      x5, 0
    li      x6, 0
    li      x8, 0
    li      x9, 0
    li      x10, 0
    li      x11, 0
    li      x12, 0
    li      x13, 0
    li      x14, 0
    li      x15, 0
    li      x16, 0
    li      x17, 0
    li      x18, 0
    li      x19, 0
    li      x20, 0
    li      x21, 0
    li      x22, 0
    li      x23, 0
    li      x24, 0
    li      x25, 0
    li      x26, 0
    li      x27, 0
    li      x28, 0
    li      x29, 0
    li      x30, 0
    li      x31, 0
    /* a0 = the boot handle set; t2 (x7) is scratch and clears itself. */
    mv      a0, t2
    li      x7, 0
    mret

/* ---- reservations -------------------------------------------------------- */

/* The one trap frame. Uniprocessor kernel, one user thread (spec §7) — the
 * per-Thread frame arrives with the object model. */
    .section .bss.trapframe, "aw", @nobits
    .balign 16
    .globl _trapframe
_trapframe:
    .skip TF_SIZE

/* 64 KiB kernel stack, reserved in .bss (zeroed by the loop above). */
    .section .bss.stack, "aw", @nobits
    .balign 16
    .globl _stack_top
_stack_bottom:
    .skip 0x10000
_stack_top:
