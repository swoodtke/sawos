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
    /* The saved processor state a user context resumes in is NOT here any more:
     * it is a word of the frame, written by `frame_init` in lib.saw, because a
     * thread carries the state it resumes in (design 178 M2 unit 2). D,A,F
     * masked and I NOT is still the value — interrupts are enabled at the
     * user-mode return and nowhere else (D2). */
    .equ CPACR_FPEN,  (3 << 20)
    /* The high half of the cause the interrupt entry calls `ktrap` with. The
     * syndrome register is 32 bits, so this value cannot be one; `lib.saw`'s
     * `IRQ_CAUSE` is the same description and must move with it. */
    .equ IRQ_CAUSE_HI, 1

/* ---- the higher-half bring-up (sawos design 29) ---------------------------
 *
 * THE ORDER IS FORCED, and it is worth stating before the code because every
 * line below is a consequence of it. This kernel LINKS in the high half
 * (`virt.ld` places every section at `physical + LINMAP_OFFSET`), so NO COMPILED
 * CODE — C or Saw — may be called until the MMU is on and TTBR1 is walked: a
 * `bl` reaches a high address, and a compiled body reaches its globals through
 * PC-relative pairs that would resolve against whatever address it is running
 * at. The MMU therefore has to come up on a table built by HAND-WRITTEN
 * ASSEMBLY, which is what the block below is, and it is the whole of the
 * relocation window.
 *
 * THE BOOT TABLE IS TWO DESCRIPTORS. At the 4 KiB granule with a 39-bit VA,
 * level 1 covers 1 GiB per entry — so one entry for the device gigabyte and one
 * for RAM's is the entire map. It serves BOTH halves at once: `pa | OFFSET`
 * leaves the low 39 bits alone, so a high kernel address and its physical twin
 * select the SAME level-1 entry, and installing the table in TTBR0 and TTBR1
 * together makes the identity map and the (coarse) linear map one page of
 * memory. That is what makes the jump into the high half a plain branch.
 *
 * THEN THE REAL TABLES. `sos_mmu_tables` (sink.c -> lib.saw) builds the linear
 * map and `sos_page_tables_build` the per-process sets, both with translation
 * already on, both writing descriptors through the seam. Installing TTBR1
 * happens from the IDENTITY MAP — the branch to `.Lswap_phys` below — for the
 * ordinary reason a kernel does that: code that changes the register its own PC
 * translates through has nowhere to stand. This is Linux's
 * `idmap_cpu_replace_ttbr1` shape, and once TTBR1 holds the real linear map the
 * identity map has done its job and TTBR0 becomes root's set.
 *
 * THE VALUES ARE NOT SPELLED HERE. `sos_boot_mair` and `sos_boot_tcr` are
 * `const u64`s in sink.c, read out of `.rodata` through a masked pointer while
 * the MMU is off (`.rodata` is loaded at that physical address). The MAIR copy
 * is checked against `lib.saw`'s `sos_mair_value()` on every boot. The
 * descriptor bits below are this file's own and are the one duplication left —
 * a wrong one is an immediate, visible boot failure rather than a silent one.
 */

    /* Block descriptors for the two boot entries, at level 1.
     *
     * The ATTRIBUTE INDICES are `MemoryType`'s in lib.saw: 0 Device-nGnRnE,
     * 1 Normal write-back. `sos_boot_mair` is the register those index, and
     * sink.c checks it against the Saw builder on every boot. */
    .equ DESC_VALID,     (1 << 0)   /* bit 0 set, bit 1 clear = BLOCK          */
    .equ DESC_ATTR_DEV,  (0 << 2)   /* AttrIndx = MemoryType.Device            */
    .equ DESC_ATTR_NORM, (1 << 2)   /* AttrIndx = MemoryType.Normal            */
    .equ DESC_SH_INNER,  (3 << 8)   /* inner shareable                         */
    .equ DESC_AF,        (1 << 10)  /* access flag                             */
    .equ DESC_PXN,       (1 << 53)  /* no execute at EL1                       */
    .equ DESC_UXN,       (1 << 54)  /* no execute at EL0                       */
    /* AP = 00: EL1 read/write, EL0 no access. Global (nG clear): the boot map
     * belongs to the kernel, not to an address space. */
    .equ BOOT_DESC_DEV,  (DESC_VALID | DESC_ATTR_DEV | DESC_AF | DESC_PXN | DESC_UXN)
    .equ BOOT_DESC_RAM,  (DESC_VALID | DESC_ATTR_NORM | DESC_SH_INNER | DESC_AF | DESC_UXN)
    .equ RAM_BASE,       0x40000000

    .equ SCTLR_M,        (1 << 0)   /* MMU on            */
    .equ SCTLR_C,        (1 << 2)   /* data cache        */
    .equ SCTLR_I,        (1 << 12)  /* instruction cache */

    .section .text.boot, "ax", @progbits
    .globl _start
_start:
    /* RUNNING AT THE PHYSICAL ADDRESS, MMU OFF. Every symbol a literal pool
     * hands us is a HIGH address from here until the jump below, so each one is
     * masked down with `LINMAP_PHYS_MASK` (an absolute symbol `virt.ld`
     * exports — loading its "address" loads its value). x9 holds the mask for
     * the whole window. */
    ldr     x9, =LINMAP_PHYS_MASK

    /* A stack, at its physical address. Nothing here calls anything, but a
     * window with no stack is a window where a fault has nowhere to go. */
    ldr     x0, =_stack_top
    and     x0, x0, x9
    mov     sp, x0

    /* FP/SIMD access — see the header note. Must precede any compiled code. */
    mov     x0, #CPACR_FPEN
    msr     cpacr_el1, x0
    isb

    /* Zero .bss [_bss_start, _bss_end) a doubleword at a time, physically.
     *
     * AUDIT (design 1): no preemption point, here or in the table builds below.
     * All of it runs before kmain and before the GIC comes up — pre-IRQ by
     * construction. */
    ldr     x0, =_bss_start
    and     x0, x0, x9
    ldr     x1, =_bss_end
    and     x1, x1, x9
1:  cmp     x0, x1
    b.hs    2f
    str     xzr, [x0], #8
    b       1b
2:
    /* The boot table lives in .bss, so it is built AFTER the zeroing. Two
     * level-1 block descriptors: entry 0 the device gigabyte at 0, entry 1
     * RAM's gigabyte at 0x4000_0000. */
    ldr     x0, =_boot_l1
    and     x0, x0, x9
    ldr     x1, =BOOT_DESC_DEV
    str     x1, [x0]
    ldr     x1, =(BOOT_DESC_RAM | RAM_BASE)
    str     x1, [x0, #8]

    /* MAIR and TCR, out of sink.c's `.rodata` at its load address. */
    ldr     x1, =sos_boot_mair
    and     x1, x1, x9
    ldr     x1, [x1]
    msr     mair_el1, x1
    ldr     x1, =sos_boot_tcr
    and     x1, x1, x9
    ldr     x1, [x1]
    msr     tcr_el1, x1

    /* ONE TABLE IN BOTH BASE REGISTERS — see the header: a high address and its
     * physical twin index the same level-1 entry, so this is the identity map
     * and a coarse linear map at once. */
    msr     ttbr0_el1, x0
    msr     ttbr1_el1, x0
    dsb     ish
    isb

    /* Read-modify-write rather than a constant: the reset value carries the
     * stack-alignment and endianness bits this kernel has no opinion about.
     * The three bits together are not a logical immediate, so they arrive in a
     * register. */
    mrs     x1, sctlr_el1
    ldr     x2, =(SCTLR_M | SCTLR_C | SCTLR_I)
    orr     x1, x1, x2
    msr     sctlr_el1, x1
    isb

    /* INTO THE HIGH HALF. An absolute branch through a literal — the one
     * instruction the whole relocation window exists for. */
    ldr     x0, =.Lhigh
    br      x0

.Lhigh:
    /* RUNNING HIGH. Every symbol is its own link address from here on, and
     * ordinary compiled code is callable. */
    ldr     x0, =_stack_top
    mov     sp, x0

    /* Exception vectors. VBAR_EL1 wants a 2 KiB-aligned table, and holds a
     * VIRTUAL address — so it is set here rather than before the MMU, where it
     * would have named an address nothing could translate. */
    ldr     x0, =_vectors
    msr     vbar_el1, x0

    /* The real tables, built with translation on. x19/x20 are callee-saved, so
     * they survive the second call. */
    bl      sos_mmu_tables              /* -> the linear map's level-1 base   */
    mov     x19, x0
    bl      sos_page_tables_build       /* -> process set 0's level-1 base    */
    mov     x20, x0

    /* Install TTBR1 FROM THE IDENTITY MAP. Branching low first is what gives
     * the swap somewhere to stand: the instruction after `msr ttbr1_el1` is
     * fetched through TTBR0, which this is not touching. */
    ldr     x9, =LINMAP_PHYS_MASK
    ldr     x0, =.Lswap_phys
    and     x0, x0, x9
    br      x0

.Lswap_phys:
    msr     ttbr1_el1, x19
    isb
    tlbi    vmalle1
    dsb     nsh
    isb
    ldr     x0, =.Lhigh_final
    br      x0

.Lhigh_final:
    /* Back in the high half, now through the real linear map. TTBR0 becomes
     * set 0's — `ROOT_PROCESS`'s, which the boot door re-installs by name — and
     * the identity map is gone with it. From here the low half is the user's
     * alone, which is the whole point of the unit. */
    msr     ttbr0_el1, x20
    isb
    tlbi    vmalle1
    dsb     nsh
    isb

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
    ventry_to user_irq_entry         /* 0x480  lower EL, AArch64, IRQ       */
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
/* A syscall and an interrupt save and restore the SAME frame and differ only in
 * what the kernel is called with, so the save is a macro used twice and the
 * restore is shared code both fall into — a second copy of thirty stores is
 * how the two paths would drift.
 *
 * DESIGN 178 M2 UNIT 2 — THE FRAME IS A THREAD'S CONTEXT. `SP_EL1` between
 * traps points at the TOP of the running thread's frame, wherever the kernel
 * put it, so the `sub` below lands exactly on that frame. The kernel's own
 * stack is a different reservation and the macro switches to it once the frame
 * is written, because everything after that point is Saw and needs a stack the
 * frame is not sitting on. */
    .macro save_user_frame
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

    /* Park the frame address in x3 and move SP_EL1 to the KERNEL stack: the
     * frame is a thread's storage, not a stack, and `ktrap` needs one. */
    mov     x3, sp
    ldr     x4, =_stack_top
    mov     sp, x4
    .endm

    .section .text.trap, "ax", @progbits
    .globl user_trap_entry
user_trap_entry:
    save_user_frame

    /* ktrap(frame, cause, tval): the cause is ESR, the address is FAR. It
     * RETURNS the frame to resume in x0 — this one, or another thread's. */
    mov     x0, x3
    mrs     x1, esr_el1
    mrs     x2, far_el1
    bl      ktrap
    b       .Luser_return

/* An interrupt taken at EL0 (design 178 M2 unit 1). It has a vector of its own,
 * so there is no syndrome register to pass and nothing to decode: `ktrap` is
 * called with a cause value the syndrome register cannot hold, which is what
 * `lib.saw`'s `is_interrupt` recognizes. WHICH line it was comes from the
 * interrupt controller, not from here.
 *
 * There is deliberately no EL1 counterpart: an interrupt taken in kernel mode
 * lands on `kernel_fault_entry` through the vector table, because the kernel
 * runs with interrupts masked and one arriving there is a bug (D2). */
    .globl user_irq_entry
user_irq_entry:
    save_user_frame

    mov     x0, x3
    movz    x1, #IRQ_CAUSE_HI, lsl #32
    mov     x2, xzr
    bl      ktrap

    /* Resume the context x0 names. A returning syscall has had its (status,
     * value) pair written into that frame's x0/x1 slots; ELR already points
     * past the `svc`, so nothing here advances it — and an interrupted
     * instruction has not run at all, so nothing may.
     *
     * THE FRAME NEED NOT BE THE ONE WE CAME IN ON. `ktrap` returns whichever
     * thread is to run, so pointing SP_EL1 at it here is the whole of the
     * context switch — and the `add` at the bottom leaves SP_EL1 at that
     * frame's top, which is where the next trap's `sub` expects to land. */
.Luser_return:
    mov     sp, x0
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

/* void sos_resume_frame(u64 frame_addr) — never returns.
 *
 * Enter user mode in the context `frame_addr` holds. This is the FIRST entry
 * only: every entry after it is `ktrap` returning a frame to the path above,
 * which is why this is one branch INTO that path rather than a second copy of
 * it. It replaced `sos_enter_user`, which built a user context by zeroing
 * thirty registers in assembly and setting three system registers — the same
 * context is now built by `frame_init` in Saw, SPSR included, and the restore
 * path loads all of it. */
    .section .text.enter, "ax", @progbits
    .globl sos_resume_frame
sos_resume_frame:
    b       .Luser_return

/* ---- reservations -------------------------------------------------------- */

/* THE KERNEL STACK IS NOT RESERVED HERE ANY MORE (sawos design 38). It was a
 * `.skip 0x10000` in a `.bss.stack` section, which put the one number a board
 * might want to change in the one file a board variant does not get to edit;
 * `virt.ld` reserves it inside `.bss` now, under `KERNEL_STACK_SIZE`, 16-byte
 * aligned as `sp` requires, and `_stack_top` is a linker symbol. Nothing about
 * how this file USES it changed — all three entry points still
 * `ldr x0, =_stack_top`. Unlike the RISC-V profile there is still no separate
 * trap-frame reservation: the frame is built on this stack.
 */

/* THE BOOT TABLE (sawos design 29): one page, two live descriptors, used for
 * exactly the window between the MMU coming on and the real linear map being
 * installed. Dead memory afterwards — 4 KiB is cheaper than the alternatives,
 * which are running compiled code before translation exists or teaching the
 * assembly to build the real map.
 *
 * In `.bss` and therefore zeroed by the loop above, which is what leaves the
 * other 510 entries INVALID; it is built after that loop for the same reason.
 * A translation table must be page-aligned, and here that is the assembler's
 * job rather than a run-time round-up. */
    .section .bss.boottable, "aw", @nobits
    .balign 4096
    .globl _boot_l1
_boot_l1:
    .skip 4096
