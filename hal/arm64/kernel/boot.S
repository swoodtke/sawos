/* SOS boot shim + exception vectors (design 162) — arm64, QEMU `virt`, EL1.
 *
 * QEMU (`-M virt -cpu cortex-a53 -kernel sos.elf`) loads the ELF at RAM base
 * 0x4000_0000 and starts the CPU AT EL1 — there is no EL3 firmware and no
 * EL2 hypervisor in this configuration, so unlike the RISC-V profile there is
 * no SBI to negotiate with and nothing to drop down from. `.text.boot` (this
 * file) is placed first by virt.ld, so the ELF entry is the reset target.
 *
 * The shim sets up the stack, installs the vector table, enables FP/SIMD,
 * zeroes .bss, builds the identity map, and calls the Saw `kmain`.
 *
 * WHY FP/SIMD IS ENABLED HERE (and this is not optional): out of reset
 * `CPACR_EL1.FPEN` traps every Advanced-SIMD instruction at EL1, and the C and
 * Saw halves are both compiled by LLVM, which vectorizes ordinary loops. So the
 * FIRST table-filling loop in `sos_mmu_init` took an EC=0x07 trap before this
 * line existed — before the vectors could even report it. FP/SIMD STATE IS NOT
 * SAVED ACROSS A TRAP (the frame below holds x0-x30 and three system
 * registers); with one user thread and no preemption nothing can observe that,
 * and M2's context switch is where it stops being true.
 *
 * THE TRAP SPLIT IS FREE HERE. RISC-V needs `mscratch` as a mode witness to
 * tell a syscall from a kernel bug; arm64 has SIXTEEN vectors, four per source
 * (current EL on SP0, current EL on SPx, lower EL in AArch64, lower EL in
 * AArch32), so "did this come from user mode" is which ENTRY the hardware
 * chose. Every entry but the lower-EL synchronous one is a kernel bug or an
 * interrupt M1 never enables, and they all land on `kernel_fault_entry`.
 *
 * Trap frame layout (34 doublewords, 272 bytes), matching `TrapFrame` in
 * lib.saw field for field:
 *
 *   +0x000  ELR_EL1   the PC to resume at (already PAST the `svc`)
 *   +0x008  SPSR_EL1  saved processor state
 *   +0x010  SP_EL0    the user stack pointer (a separate register at EL1)
 *   +0x018  x0 .. x30
 */

    .equ FRAME_SIZE,  272
    .equ SPSR_EL0T,   0x3c0     /* D,A,I,F masked; M[3:0] = 0 -> EL0t */
    .equ CPACR_FPEN,  (3 << 20)

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* Stack: the 64 KiB reservation below (in .bss), top-aligned. */
    ldr     x0, =_stack_top
    mov     sp, x0

    /* Exception vectors. VBAR_EL1 wants a 2 KiB-aligned table. */
    ldr     x0, =_vectors
    msr     vbar_el1, x0

    /* FP/SIMD access — see the header note. Must precede any compiled code. */
    mov     x0, #CPACR_FPEN
    msr     cpacr_el1, x0
    isb

    /* Zero .bss [_bss_start, _bss_end) a doubleword at a time. */
    ldr     x0, =_bss_start
    ldr     x1, =_bss_end
1:  cmp     x0, x1
    b.hs    2f
    str     xzr, [x0], #8
    b       1b
2:
    /* The page tables live in .bss, so this must follow the zeroing. */
    bl      sos_mmu_init

    /* Enter the Saw kernel. It stops the machine and does not return; if it
     * ever does, hang rather than execute past the image. */
    bl      kmain
3:  wfi
    b       3b

/* ---- exception vectors --------------------------------------------------- */

    .macro ventry_to label
    .balign 0x80
    b       \label
    .endm

    .section .text.vectors, "ax", @progbits
    .balign 0x800
    .globl _vectors
_vectors:
    ventry_to kernel_fault_entry     /* 0x000  current EL, SP0, synchronous */
    ventry_to kernel_fault_entry     /* 0x080  current EL, SP0, IRQ         */
    ventry_to kernel_fault_entry     /* 0x100  current EL, SP0, FIQ         */
    ventry_to kernel_fault_entry     /* 0x180  current EL, SP0, SError      */
    ventry_to kernel_fault_entry     /* 0x200  current EL, SPx, synchronous */
    ventry_to kernel_fault_entry     /* 0x280  current EL, SPx, IRQ         */
    ventry_to kernel_fault_entry     /* 0x300  current EL, SPx, FIQ         */
    ventry_to kernel_fault_entry     /* 0x380  current EL, SPx, SError      */
    ventry_to user_trap_entry        /* 0x400  lower EL, AArch64, sync      */
    ventry_to kernel_fault_entry     /* 0x480  lower EL, AArch64, IRQ       */
    ventry_to kernel_fault_entry     /* 0x500  lower EL, AArch64, FIQ       */
    ventry_to kernel_fault_entry     /* 0x580  lower EL, AArch64, SError    */
    ventry_to kernel_fault_entry     /* 0x600  lower EL, AArch32, sync      */
    ventry_to kernel_fault_entry     /* 0x680  lower EL, AArch32, IRQ       */
    ventry_to kernel_fault_entry     /* 0x700  lower EL, AArch32, FIQ       */
    ventry_to kernel_fault_entry     /* 0x780  lower EL, AArch32, SError    */

/* ---- the user-mode trap path --------------------------------------------- */
/*
 * Entered with SP_EL1 already selected and pointing at the kernel stack — EL1
 * has its own stack pointer, so there is no swap to perform and no window in
 * which the kernel is running on the user's stack. The frame is built ON that
 * kernel stack rather than in a static reservation, which is what a per-thread
 * frame will want when threads exist.
 */
    .section .text.trap, "ax", @progbits
    .globl user_trap_entry
user_trap_entry:
    sub     sp, sp, #FRAME_SIZE
    stp     x0,  x1,  [sp, #(3*8)]
    stp     x2,  x3,  [sp, #(5*8)]
    stp     x4,  x5,  [sp, #(7*8)]
    stp     x6,  x7,  [sp, #(9*8)]
    stp     x8,  x9,  [sp, #(11*8)]
    stp     x10, x11, [sp, #(13*8)]
    stp     x12, x13, [sp, #(15*8)]
    stp     x14, x15, [sp, #(17*8)]
    stp     x16, x17, [sp, #(19*8)]
    stp     x18, x19, [sp, #(21*8)]
    stp     x20, x21, [sp, #(23*8)]
    stp     x22, x23, [sp, #(25*8)]
    stp     x24, x25, [sp, #(27*8)]
    stp     x26, x27, [sp, #(29*8)]
    stp     x28, x29, [sp, #(31*8)]
    str     x30,      [sp, #(33*8)]

    mrs     x0, elr_el1
    mrs     x1, spsr_el1
    mrs     x2, sp_el0
    stp     x0, x1, [sp, #0]
    str     x2,     [sp, #16]

    /* ktrap(frame, cause, tval): the cause is ESR, the address is FAR. */
    mov     x0, sp
    mrs     x1, esr_el1
    mrs     x2, far_el1
    bl      ktrap

    /* Resume whatever context the frame now describes. A returning syscall has
     * had its (status, value) pair written into the x0/x1 slots; ELR already
     * points past the `svc`, so nothing here advances it. */
    ldp     x0, x1, [sp, #0]
    msr     elr_el1, x0
    msr     spsr_el1, x1
    ldr     x2, [sp, #16]
    msr     sp_el0, x2

    ldp     x0,  x1,  [sp, #(3*8)]
    ldp     x2,  x3,  [sp, #(5*8)]
    ldp     x4,  x5,  [sp, #(7*8)]
    ldp     x6,  x7,  [sp, #(9*8)]
    ldp     x8,  x9,  [sp, #(11*8)]
    ldp     x10, x11, [sp, #(13*8)]
    ldp     x12, x13, [sp, #(15*8)]
    ldp     x14, x15, [sp, #(17*8)]
    ldp     x16, x17, [sp, #(19*8)]
    ldp     x18, x19, [sp, #(21*8)]
    ldp     x20, x21, [sp, #(23*8)]
    ldp     x22, x23, [sp, #(25*8)]
    ldp     x24, x25, [sp, #(27*8)]
    ldp     x26, x27, [sp, #(29*8)]
    ldp     x28, x29, [sp, #(31*8)]
    ldr     x30,      [sp, #(33*8)]
    add     sp, sp, #FRAME_SIZE
    eret

/* A trap the KERNEL took, i.e. a kernel bug. There is no context worth saving:
 * report the cause and stop the machine non-zero. (M0's discipline, unchanged:
 * a kernel fault never hangs.)
 *
 * The three system registers are read HERE because `mrs` names a register at
 * assembly time; `sos_kernel_fault` is Saw since design 172 unit 3 and takes
 * them as ordinary arguments. */
    .globl kernel_fault_entry
kernel_fault_entry:
    mrs     x0, esr_el1
    mrs     x1, elr_el1
    mrs     x2, far_el1
    bl      sos_kernel_fault
4:  wfi
    b       4b

/* ---- EL1 -> EL0 ---------------------------------------------------------- */

/* void sos_enter_user(u64 entry, u64 stack_top, u64 boot_handle) — never
 * returns.
 *
 * Points ELR at the user entry, gives EL0 its own stack through SP_EL0, selects
 * EL0t as the `eret` target with interrupts masked (M1 has no interrupt
 * sources), and hands the user a zeroed register file so nothing of the
 * kernel's leaks across the privilege boundary.
 *
 * The one register that is NOT zeroed is x0, which carries the boot handle set
 * (spec §12). In M1 that is a single handle — root's System handle — so it fits
 * in a register; when the set grows it becomes a structure the kernel places in
 * root's memory and x0 points at. Either way this is the handoff: root's entry
 * takes it as its first C-ABI argument, so a Saw `_start(boot_handle: UInt)`
 * just receives it. */
    .section .text.enter, "ax", @progbits
    .globl sos_enter_user
sos_enter_user:
    msr     elr_el1, x0
    msr     sp_el0, x1
    mov     x3, #SPSR_EL0T
    msr     spsr_el1, x3
    mov     x0, x2
    mov     x1, xzr
    mov     x2, xzr
    mov     x3, xzr
    mov     x4, xzr
    mov     x5, xzr
    mov     x6, xzr
    mov     x7, xzr
    mov     x8, xzr
    mov     x9, xzr
    mov     x10, xzr
    mov     x11, xzr
    mov     x12, xzr
    mov     x13, xzr
    mov     x14, xzr
    mov     x15, xzr
    mov     x16, xzr
    mov     x17, xzr
    mov     x18, xzr
    mov     x19, xzr
    mov     x20, xzr
    mov     x21, xzr
    mov     x22, xzr
    mov     x23, xzr
    mov     x24, xzr
    mov     x25, xzr
    mov     x26, xzr
    mov     x27, xzr
    mov     x28, xzr
    mov     x29, xzr
    mov     x30, xzr
    eret

/* ---- reservations -------------------------------------------------------- */

/* 64 KiB kernel stack, reserved in .bss (zeroed by the loop above). Unlike the
 * RISC-V profile there is no separate trap-frame reservation: the frame is
 * built on this stack. */
    .section .bss.stack, "aw", @nobits
    .balign 16
_stack_bottom:
    .skip 0x10000
    .globl _stack_top
_stack_top:
