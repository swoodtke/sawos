# arm64 kernel HAL — the seam

What the SOS kernel needs from its architecture, and nothing more. This
directory implements the same list `hal/riscv32/kernel/ABI.md` states, for
Profile B (spec §5b): kernel at EL1, processes at EL0, QEMU `virt` with
`-cpu cortex-a53`. The kernel above it does not change, and
`tools/sos_runner.py` checks that by scanning `kernel/core/` for architecture
names and failing the run if it finds one.

The seam has two halves. The Saw half (`lib.saw`) is the module the kernel
imports as `hal`. The native half (`boot.S`, `sink.c`) is what Saw cannot
express: a vector table, an `eret`, a system-register write, a semihosting
call, and a linker symbol.

Almost everything is Saw: the PL011 write loop, the page-table construction,
the grant editing, and the kernel-fault report with its hex formatting. The
`sink.c` functions each state their reason in the table below: an instruction
with no Saw spelling, a linker symbol's address, or (`sos_mmu_tables`) a
sequencing shim between Saw and assembly.

This profile's timer is system registers, so reading its frequency and counter,
reading and writing its control, and setting its compare value are
instructions, one line each. Everything above them is Saw: the nanosecond
deadline arithmetic, the tick policy, and the whole interrupt controller, which
is memory-mapped.

A thread context is a frame the kernel owns, built by `frame_init` in Saw with
its `SPSR` among the words. The privilege transition is a single branch into
the trap entry's own restore path.

## The Saw surface (`lib.saw`)

Identical in name and meaning to the riscv32 HAL's. That table is not repeated
here, and neither is the interrupt half beside it (the timer, the controller,
and the one funnel above them). What follows is only what this profile does
differently, and why.

Four names in that table are answered differently here, which is why the
kernel reads a HAL number rather than a shared constant:

| Name | What it means here |
|---|---|
| `GRANT_ROW_BUDGET: UInt` | **A policy cap, not a hardware one.** There are no numbered regions to run out of — a grant is a walk of page descriptors — so what bounds a domain is the kernel's fixed-size grant record, and this restates its length (`kcore.limits.MAX_GRANT_ROWS`) as a HAL fact. On Profile A the same name is the physical wall. A `map()` meeting either answers `NoResource`; the difference is what a change that raises it would have to touch. |
| `map_target_ok(base, top) -> Bool` | **The 4 MiB grant window, asked rather than died in.** `prot_region` already enforces this bound by stopping the machine (`grant_outside_window`) — correct for the loader path, where a memory map that outgrew the window is a kernel bug, and wrong for a map, where the range came from a region a process chose. The predicate turns that refusal into a caller-visible `BadArg` for the op, and the HAL's kernel-bug stop stays for the loader path. Page alignment of `base` is not required: the walk rounds down to the page it starts in, exactly as it does for a segment. |
| `map_source_ok(base, top) -> Bool` | **The other half of the question above.** `map_target_ok` asks about the range that indexes the level-3 table (the input, which must fit the 4 MiB window); this asks about the frames a descriptor's output address names (which must be RAM the linear map covers, `RAM_LEN`). On both riscv32 boards the two have the same body — where a user address is its physical address there is only one question — and that identity is the tier split visible in a single file. |
| `device_window_ok(base, len) -> Bool` | Whole pages, both ends, inside the one device window this board publishes a level-3 table for. Profile A's rule differs in form (a naturally-aligned power of two, because a window there is one protection entry) and is identical in purpose, which is what makes the predicate per-HAL rather than a shared check with two branches. |

A second group is where this profile's answers are not the identity:
placement, expressed as one constant and two functions rather than as a tier
flag the kernel branches on. Only this profile has the constant.

| Name | What it means here |
|---|---|
| `USER_IMAGE_BASE: UInt` | `0x4020_0000`. **The one virtual base every user image on this board is linked at**, mirrored by `hal/arm64/user/user.ld`, the single user linker script. Root, a child and a grandchild all see their own image here, because each runs under its own table set and the whole low half belongs to whichever process is current. Its value equals `ROOT_LOAD_BASE` on purpose, which keeps root's `entry=` console row at that address. Nothing at build time can compare it with the linker script (a Saw static's value is not a linker symbol); the loader's `check_segment` catches a disagreement with a named diagnostic. |
| `image_link_base(dest_base) -> UInt` | `USER_IMAGE_BASE`, whatever the frames are. `dest_base` is ignored here and is the answer on the MPU tier — the seam takes it so that an identity body has an argument to return and the kernel needs no tier conditional. |
| `map_place(cursor, pa, device) -> UInt` | `cursor` for RAM: the address is the kernel's answer, and `map` takes no VA hint on any tier. `pa` for a device row, which is a statement about this board rather than a limitation of the seam — MMIO lives in a level-3 window of its own indexed by the hardware address, and the addresses in it are the ones the board's region table publishes. A translating tier could place MMIO; this one does not. |

The `cursor` it is handed is `kcore.process.free_user_va`'s answer: first fit at
or above the target's own region top, over the rows that target already has.
That policy is the kernel's and is stated there, not here — this seam only says
whether the board uses it.

One more pair is shared by all HALs and does something only on this profile:

| Name | What it means here |
|---|---|
| `phys_to_virt(pa) -> UInt` | `pa \| LINMAP_OFFSET`. **A physical address as the kernel must spell it to dereference it.** The kernel runs in the high half under TTBR1 over a linear map of RAM and MMIO, so a stored physical address — a sosimg record, a region-table row, a grant's bounds, an MMIO base, a translation table — is not an address the kernel can load through. Both riscv32 HALs answer the identity and fold the call away entirely. |
| `virt_to_phys(va) -> UInt` | `va & LINMAP_PHYS_MASK`, the inverse, for the three places a kernel address has to become hardware's or data's: the table pool's base (a TTBR and every table descriptor hold physical), the payload and region-table linker bounds, and a region row the linker resolved. Idempotent on an already-physical address, which is what lets one call normalize the region table's mixed-provenance rows. |

The discipline above them is the kernel's, not this file's, and it keeps
`kernel/core` arch-free: addresses-as-data stay physical everywhere, and only
the moment of kernel dereference converts. `LINMAP_OFFSET` is
`0xFFFF_FF80_0000_0000` (`2^64 - 2^39`, matching `TCR_EL1.T1SZ` = `T0SZ` = 25),
and `static_assert`s hold this board's RAM and MMIO under it so the pair
stays a pure OR/AND.

The poll-and-place, the panic path's write loop and the exit-status promotion
are `sosrt`'s, once, for both profiles. What stays here is the device, and the
PL011's polarity is the reason this is a trait rather than a shared function:
readiness is FR bit 5 clear (transmit FIFO not full), the opposite sense to the
16550's LSR bit 5 set, and `put` stores a `UInt32` to DR rather than a byte to
THR.

## The native half

| Symbol | Where | Contract | Why not Saw |
|---|---|---|---|
| `_start` (entered as `_start_phys`) | boot.S | Reset entry, and the whole relocation window. At its physical address, MMU off: a masked stack, `CPACR_EL1.FPEN`, `.bss` zerofill, and a two-descriptor boot table installed in TTBR0 and TTBR1 at once — one page serving as both the identity map and a coarse linear map, because a kernel address and its physical twin share a level-1 index. Then MMU on, branch high, set the real stack and `VBAR_EL1`, build the real tables (`sos_mmu_tables`, `sos_page_tables_build`), install TTBR1 from the identity map, install TTBR0 = set 0, `kmain`. Never returns. `ENTRY` names `_start_phys` (`_start - LINMAP_OFFSET`) because QEMU sets the reset PC from `e_entry` with the MMU off. | Instructions: `msr`, and — the reason the window exists at all — a high-linked kernel cannot call compiled code until the MMU is on and TTBR1 is walked. |
| `_vectors` | boot.S | The 16-entry EL1 vector table. Two entries are a user trap: "lower EL, AArch64, synchronous" and "lower EL, AArch64, IRQ". Every other entry is a kernel bug (an interrupt among them, see "Interrupts are masked in the kernel" below) and lands on `kernel_fault_entry`. | A vector table is placement + branches at fixed 0x80 strides. |
| `user_trap_entry` | boot.S | Saves the running thread's 34-doubleword frame, switches `SP_EL1` to the kernel stack, calls `ktrap(frame, ESR, FAR)`, and resumes the frame `ktrap` returns — which need not be the one it was called with, and that is the context switch. | Register saves and `eret`. |
| `user_irq_entry` | boot.S | The same frame, then `ktrap(frame, IRQ_CAUSE, 0)`, then the same return path — the save is a macro and the restore is shared code, so the two entries cannot drift. | Register saves and `eret`. |
| `kernel_fault_entry` | boot.S | Reads `ESR`/`ELR`/`FAR` and calls the Saw `sos_kernel_fault` with them. | `mrs` names a register at assembly time. The report is Saw. |
| `sos_resume_frame(frame)` | boot.S | Enter EL0 in a saved context, behind `resume_frame`. One branch into the restore path above, because the frame already holds the `SPSR` that selects EL0t. | The `eret` the restore path owns. |
| `sos_platform_exit(code)` | sink.c | Stop the machine through semihosting `SYS_EXIT`. | `hlt #0xf000` with the call number and parameter block pinned in x0/x1. |
| `sos_mmu_tables()` | sink.c | Build the kernel linear map and hand back its level-1 physical base, for `boot.S` to install in TTBR1. Checks `sos_boot_mair` against `lib.saw`s `sos_mair_value()` first and stops the machine (68) on drift. Called from the high half, so the tables below it are built with translation already on. `boot.S` enables the MMU, not this function. | A sequencing shim between two Saw builders and one assembly caller. |
| `sos_prot_commit()` | sink.c | Publish the staged grant set. It serves only the legacy harness kernels in `tests/` that program the protection surface directly; the kernel's own switch is `sos_ttbr0_write`. | `dsb`/`isb` barriers and a `tlbi`. The descriptors are Saw. |
| `sos_ttbr0_write(value)` | sink.c | **The process switch**: install a persistent per-process table set. `value` is the set's level-1 base with the ASID already in bits 63:48 — one word, assembled in Saw. | `msr` names a system register at assembly time; the `dsb ishst`/`isb` around it are barriers. |
| `sos_tlbi_asid(asid)` | sink.c | Drop every cached translation carrying this ASID — the maintenance behind a grant install, a revocation, and a domain clear. Shifts the argument into bits 63:48 itself. | `tlbi` is a maintenance instruction and the fences are barriers. |
| `sos_timer_freq()` / `sos_timer_ctl_read()` / `sos_timer_ctl_write(v)` / `sos_timer_count()` / `sos_timer_set_compare(v)` | sink.c | The core's physical timer: its frequency, its control register (enabled / masked / fired), the free-running 64-bit counter, and the 64-bit absolute deadline it is compared against. | `mrs`/`msr` name a system register at assembly time. One instruction each; the nanosecond arithmetic, the tick policy and the deadline composition are Saw. |
| `sos_payload_start()` / `sos_payload_end()` | sink.c | Bounds of the appended payload, **masked to physical**: the symbol is a high link address, but these bounds are granted to EL0 and read as an image base, which are both physical readings. | A linker symbol's address, which Saw cannot name. |
| `sos_region_table_start()` / `sos_region_table_end()` | sink.c | Bounds of the `.regions` section — the boot region table. The table's blob rows carry bases the linker resolved when it placed the generated stub, so no per-child symbol has to be nameable here. | Same reason. |
| `sos_wait_for_irq()` | sink.c | Park the core until an interrupt is pending, behind `wait_for_irq`. | `wfi` is an instruction. |
| `virt.ld` | — | Links the image at `0x4000_0000 + LINMAP_OFFSET` and loads it at 0x4000_0000 (one VMA cursor, a per-section `AT(ADDR(.x) - LINMAP_OFFSET)`), bounds the appended payload on page boundaries, and exports `LINMAP_PHYS_MASK` as an absolute symbol for `boot.S`. Asserts `_bss_end` below `ROOT_LOAD_BASE` — which is also the top of the one RAM block the linear map leaves executable. | Not a program. |

`sos_rt_write` (check-free by construction so the panic path cannot re-enter
it), `sos_rt_abort`, `sos_kernel_fault` with its `report_str`/`report_hex`,
`prot_reset` / `prot_region` and the whole page-table build are Saw in
`lib.saw`. `sos_mmu_tables` calls the Saw `sos_linmap_build` and compares
`sos_mair_value`; `boot.S` calls the Saw `sos_page_tables_build` directly.

## Required of the kernel

- `kmain()` — the Saw entry `_start` calls.
- `ktrap(frame, cause, tval) -> UInt` — the Saw handler `user_trap_entry`
  calls. `cause` is `ESR_EL1`, `tval` is `FAR_EL1`. It returns the frame to
  resume: the same one for an ordinary return, another thread's for a switch.
  The trap entry does not know which happened and must not care.

## Thread contexts

Identical in name and meaning to the riscv32 HAL's three rows — `FRAME_BYTES`,
`frame_init`, `resume_frame` — and that table is not repeated here. Two things
are this profile's:

- **`SP_EL1` is the "current thread" pointer.** Between traps it points at the
  top of the running thread's frame, so the entry's `sub sp, sp, #FRAME_SIZE`
  lands on that frame with nothing to look up, and the return's matching `add`
  leaves it pointing at whatever frame was resumed. That is the whole of the
  switch, and it is the same trick RISC-V plays with `mscratch`. The kernel's
  own stack is a separate reservation the entry switches to once the frame is
  written, because everything past that point is Saw and needs a stack the
  frame is not sitting on.
- **The `SPSR` is a word of the frame**, not a value the transition writes. A
  thread carries the processor state it resumes in, which is what lets one
  restore path serve the first entry and every switch after it.

There is no `enter_user` on either profile: a context built in assembly and
entered once has no storage a second thread could have.

## What is arm64-specific here, and why

- **A device window is an attribute, not a place.** On Profile A a page is
  uncached because of where it is; here it is device memory because its
  descriptor says so, so a window mapped Normal would be cached, speculatively
  read and merged — a register block under that is not slow, it is wrong.
  `prot_device` writes `Device-nGnRE` (the ordinary driver attribute:
  non-Gathering, non-Reordering, early write acknowledgement permitted) and
  both no-execute bits; the kernel's own MMIO keeps the stricter `nGnRnE`. The
  access-size half cannot be expressed in a descriptor and belongs to the
  driver: device memory permits no unaligned and no multi-register access, so a
  driver reads and writes each register at its own width and never copies a
  block. **The attribute is carried by an object kind**: only an `IoMemory`
  reaches `prot_device`, and `IoMemory` has no op that could install anything
  else, so "a device region cannot produce a cacheable mapping" is a property
  of the type, not a flag the loader honours. On this profile that is the
  difference between a correctness guarantee and a convention.
- **No region budget.** Profile A's budget is a hardware wall (numbered PMP
  regions); here a domain is a page-table walk and there is nothing to widen.
  What bounds a domain instead is the kernel's grant record (`GRANT_ROW_BUDGET`
  above) and the 4 MiB grant window, and neither is hardware. The two profiles'
  row budgets mean different things, and a change that raises one has a
  different argument to make than a change that raises the other.
- **Granting one page needs page-granular tables.** A block is not a grant
  unit, and handing EL0 a 1 GiB or 2 MiB block holding the console would hand
  over every peripheral beside it. So the device space a process sees is a
  level-2 table whose one live entry is a level-3 table over the console's
  2 MiB block, which is the minimum that makes a single register page
  grantable. The page-table pool is described under the translation layout
  below.
- **No mode witness.** RISC-V needs `mscratch` (0 in the kernel, the running
  thread's frame in user mode) to tell a syscall from a kernel bug. Here the
  hardware picks a different vector per source EL, so the question is answered
  by which of sixteen entries ran. `SP_EL1` still carries the running thread's
  frame (above), but for scheduling rather than for mode.
- **`svc` traps with `ELR_EL1` already past the instruction**, so
  `syscall_return` advances nothing. Doing what Profile A does here would skip
  the instruction after a syscall — which is why "how far to resume" is a HAL
  decision rather than a kernel one.
- **FP/SIMD must be enabled before any compiled code runs, for the sake of the
  C.** `CPACR_EL1.FPEN` traps Advanced SIMD at EL1 out of reset, and LLVM
  reaches for `q` registers to move a struct, so compiled C faults at EL1
  without it. The freestanding profile implies `-neon,-fp-armv8` on aarch64, so
  the Saw half emits no SIMD instructions; the boot line stays because
  `rt/common_c/support.c` is C (its `memcpy` is the loop-idiom self-recursion
  case) and compiles to SIMD references at `-O2`. Removing it needs
  `-mgeneral-regs-only` on every aarch64 C compile, which needs a Blade manifest
  key for per-target C flags. FP/SIMD state is not saved across a trap (the
  frame holds x0-x30 and three system registers), although EL0 code may use it
  (`FPEN` is set for EL0 as well) and the kernel's own C does: `support.c`'s
  `memcpy` uses q0/q1/d0 and runs on the IRQ wake path. A syscall is safe,
  because the stub is a C call and v0-v7 are caller-saved; an asynchronous trap
  can corrupt a thread's vector registers, with no thread switch needed (open:
  SO-4).
- **Semihosting, not PSCI, for shutdown.** PSCI `SYSTEM_OFF` over the HVC
  conduit works on `-M virt` but always exits the emulator with status 0, and
  this harness asserts on exit status — one case encodes its entire verdict in
  the number. Semihosting `SYS_EXIT` carries a subcode QEMU exits with, which
  is the SiFive finisher's shape. It needs `-semihosting` on the QEMU command
  line.
- **Translation tables instead of PMP.** The pool is the static
  `PAGE_TABLES` in `lib.saw`, one region rounded up to a page at run time (a
  Saw static has no alignment attribute), laid out as:
  - `PROT_DOMAIN_SLOTS` per-process table sets, one per process slot, each
    `PT_SET_DESCRIPTORS` long: six tables, namely level 1, level 2 over RAM,
    two level-3 tables (`GRANT_PAGES` pages, the 4 MiB grant window),
    a device level 2 and a device level 3. Within a set the tables sit at
    `LEVEL2_OFFSET`, `LEVEL3_OFFSET`, `DEVICE_LEVEL2_OFFSET` and
    `DEVICE_LEVEL3_OFFSET`. A set is what `TTBR0_EL1` selects, with the slot
    index as the ASID.
  - the kernel's linear map, `LINMAP_DESCRIPTORS` long and built once: three
    tables (a level 1, a level 2 over the device gigabyte, a level 2 over
    RAM's gigabyte), with no level 3. `TTBR1_EL1` points at it.
  - one table (512 descriptors) of alignment slack, which covers the
    round-up of the base to a page whatever the number of sets. Adding a table
    to a set, or to the linear map, means re-checking the slack.

  Per-process set (`TTBR0_EL1`, user addresses only):

  | Level | Covers | State |
  |---|---|---|
  | 1 `[0]` | 0x0000_0000, the device space | table -> device level 2 |
  | 1 `[1]` | 0x4000_0000, 1 GiB of RAM | table -> level 2 |
  | 1 rest | everything else in the low half | INVALID — which is what makes `UNMAPPED_PROBE` fault |
  | device 2 | the block holding the console (`DEVICE_WINDOW_BASE`) | table -> device level 3; every other entry INVALID |
  | 2 | the first 4 MiB of RAM | tables -> level 3: the grant window; every other entry INVALID |
  | 3 | 1024 pages | reset: INVALID (`DESC_ABSENT`). Granted: EL0 RO or RW, `PXN` always |

  Kernel linear map (`TTBR1_EL1`, global, EL1 only):

  | Level | Covers | State |
  |---|---|---|
  | 1 `[0]` | the device gigabyte | table -> level 2 of 2 MiB blocks, EL1 RW, nGnRnE, never executable |
  | 1 `[1]` | RAM's gigabyte | table -> level 2 of 2 MiB blocks over `RAM_LEN`, Normal inner-shareable, EL1 RW, `UXN`; `PXN` on every block except the one holding the kernel image (`KERNEL_IMAGE_BLOCK`) |

  EL0 default-deny is the same property PMP gives for free: a page EL0 was
  never granted is absent from its set, so an access is a translation fault,
  and the kernel is reachable from itself whichever set is live because the
  low half owes it nothing. The kernel image block staying EL1-RWX is the
  arm64 spelling of Profile A's unconstrained M-mode, not a claim about kernel
  W^X — neither profile has that. The window covers the kernel image as well
  as the root region because harness cases grant a payload that executes in
  place inside it.

  A grant installs exactly what the image asked for, and `PXN` on top: the
  kernel must not execute user memory even by accident, which is a guarantee
  Profile A cannot make.
- **Table walks are cacheable and inner-shareable**, so a descriptor written
  with the MMU on is visible to the walker without cache maintenance and
  `prot_commit` only has to order and flush the TLB. The same property lets the
  per-process edits (`prot_update` / `prot_clear`) get away with a `dsb ishst`
  and one `tlbi aside1is`: they write descriptors in another process's set with
  the MMU on, and nothing has to reach memory beyond ordering the stores ahead
  of the invalidation. The page-table pool is `.bss`, which `boot.S` zeroes with
  the MMU off, and the boot table (`_boot_l1`) is written with it off too; a
  real board would want a data-cache clean first; QEMU does not model caches,
  and a board port is where that stops being free.
- **The interrupt controller is v2, and it is pinned.** `tools/sos_runner.py`
  passes `gic-version=2` rather than taking the machine's default: this HAL
  programs a v2 distributor and CPU interface, and a newer emulator changing
  its default would swap the hardware under a kernel with no way to say so. A
  v3 port is system registers instead of the CPU-interface window and is a HAL
  change, not a kernel one.
- **The timer is a controller line here** (a per-core one, `TIMER_IRQ`), which
  is the seam difference a reader trips over: Profile A's timer is a core-local
  comparator with an interrupt class of its own and never reaches its
  controller, so its `IRQ_TIMER` is a stand-in number and its `irq_complete`
  does nothing for the timer. Here every tick goes round the ordinary
  claim/complete cycle, which is also why this profile exercises that cycle
  without the selftest line and Profile A does not.
- **One compare register, two customers.** The scheduler tick and the Timer
  objects share `cntp_cval_el0`, and the sharing is the kernel's arithmetic,
  not this HAL's: the seam is `timer_now_ns()` + `timer_set_deadline_ns(at)` —
  a counter read and an absolute deadline — and `kernel/core/` programs the
  earliest deadline it owes anybody. What this machine does with such a
  deadline: converts nanoseconds to counter units at `cntfrq_el0`, rounding up
  so a deadline never fires early; writes `cntp_cval_el0` before enabling, so a
  stale compare value cannot assert in the gap; then enables `cntp_ctl_el0` and
  unmasks the timer's line (`TIMER_IRQ`) at the distributor, both idempotently.
  The kernel's "never" deadline (`UInt64.max`) saturates to a counter value this
  machine never reaches. The compare register is used rather than
  `cntp_tval_el0` because `cntp_tval_el0` is 32 bits signed and cannot express a
  deadline as far out as a Timer object can ask for.
- **Interrupts are masked in the kernel, and a stray one hits a vector.** The
  kernel runs with interrupts masked at its own exception level throughout —
  the hardware masks on every exception entry, and the boot path never unmasks
  — and the user-mode return is where they come back on: `SPSR_EL0T` leaves
  the I bit clear, and a returning trap restores the user's own saved state.
  Should an interrupt arrive at kernel level anyway, the four current-EL
  vectors send it to `kernel_fault_entry`: a diagnosed stop, never a silent
  reentry.
- **The interrupt entry passes a cause the syndrome register cannot hold.** An
  interrupt has its own vector and no syndrome, but the kernel's handler takes
  one cause word for every trap, so `user_irq_entry` passes `1 << 32` — this
  architecture's syndrome is 32 bits, so no fault or syscall can look like it.
  `boot.S`'s `IRQ_CAUSE_HI` and `lib.saw`'s `IRQ_CAUSE` are one description and
  must move together, exactly as the frame size is.
- **`irq_raise_selftest_line` raises a software-generated line**, because this
  controller has a register for exactly that. Profile A has none and has to
  make a real device interrupt instead — the same seam, answered by what each
  board can do.
