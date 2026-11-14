# SO-112: windowed threads on the ESP32-S3 (design)

The kernel stays call0 and runs windowed code beside call0 code: the six
window vectors become spill and fill handlers, the Alloca exception is
handled, the trap frame carries the whole 64-register file, every S3 thread
starts in one state both conventions run from, a fault inside a window
handler is the faulting process's, and a call0 stub lets the kernel call a
windowed function in the mask ROM.

Every claim is marked **[run]** (shown by a run on the emulator below, with
the run named) or **[read]** (read from code, a binary, a header or the ISA
as stated, and not run). "The emulator" is the Espressif QEMU named in
`hal/xtensa-esp32s3/ABI.md`; "the part" is silicon, on which nothing here
has run. The prototype this design was checked with is
`prototype.diff` beside this file (§11).

---

## 0. Verdict

The approach is sound on the emulator, and nothing found needs the scope
changed. One start state serves both conventions: PS.WOE = 1, PS.CALLINC = 1,
WINDOWBASE 0, WINDOWSTART 1, the entry argument in both `a2` and `a6`, the
stack top in `a1` and a zero return address. A call0 entry never rotates the
window, and a windowed entry's `entry` behaves as if the thread had been
`call4`ed by a frame that can be spilled into the thread's own stack and is
never returned into. No process declares its convention. **[run]**: the
whole S3 smoke (46 cases, 41 of them the existing ones) passes on the
prototype, every existing case's console is byte-identical to main's apart
from host-time values and moved code addresses, and the new windowed cases
pass with every mutant tried red but none (§8, §11).

The trap path saves and restores all 64 physical registers and never writes
a user stack. It costs 126 more instructions per trap and switch (87 to 213)
and 200 more bytes per frame (104 to 304), which stays inside the 512-byte
`THREAD_FRAME_STRIDE`, so the kernel's RAM does not change.

Two facts the next unit (SO-113, the PLL) must carry, both **[read]** from
the ROM ELF and **[run]** where marked:

- The ROM's phy function table, which `rom_i2c_writeReg` and
  `rom_i2c_writeReg_Mask` call through, lives in the ROM's RAM at
  0x3FCE_F3D4 (`.data_phyrom`, 0x298 bytes), which is inside the RAM pool a
  case may hand to processes (0x3FCE_0000-0x3FCE_FFFF, ABI.md §13a). The
  ROM may be called only before the loader runs (`clock_init`, the first
  thing `kmain` does), or the pool must move (question 4). At boot the table
  holds the ROM ELF's initial values **[run: `rom_call`]**.
- The PLL path's ROM calls nest four windowed frames below the stub at most,
  all `call8`, and write no PS **[read]**; the register file holds six such
  frames below the stub before anything overflows. The stub is safe if one
  does overflow anyway **[run: `rom_call`'s deep chain from the kernel]**.

---

## 1. Today

**Window state on a running system.**

- `_start` (`boot.S`) leaves the ROM's window at once: WINDOWSTART = 1,
  WINDOWBASE = 0, `rsync`, PS = 0x0F (INTLEVEL 15, EXCM 0, UM 0, WOE 0)
  **[read]**. Nothing in `boot.S` or `sink.c` writes WINDOWBASE or
  WINDOWSTART again or executes `rotw`, `entry`, `callN` or `retw`
  **[read]**, so the kernel runs `kmain` and every `ktrap` at WINDOWBASE 0,
  WINDOWSTART 1, PS.WOE 0.
- A process starts with PS = 0x30 from `frame_init` (UM, EXCM; `rfe` clears
  EXCM) and the window state the kernel left. A call0 C root printing its
  own registers at `_start` reads `ps=0x00000020 wb=0x00000000
  ws=0x00000001` **[run: base kernel, `tests/xtensa-windowed` built
  call0; `base_call0_state.console`]**.
- A windowed process dies at its first instruction: `entry` with PS.WOE = 0
  is an IllegalInstruction on the emulator,
  `SOS: fault illegal-instruction cause=0x00000000 epc=<its entry> ...`
  **[run: base kernel, `tests/xtensa-windowed` built windowed]**.

**The vectors.** Each of the six window vectors (VECBASE + 0x000 to 0x140,
ABI.md §5a) and the double vector (0x3C0) is `movi a2, <offset>; j
vec_fatal`, which calls `kernel_fault` and stops the machine **[read]**. No
call0 image reaches a window vector (nothing rotates) and a windowed one
never gets past its first `entry`, so none of them is reached today.

---

## 2. The ISA rules used

The Xtensa ISA reference manual is not on this machine and two fetches of a
public copy failed (a 403 and a 404), so the rules below are the ISA's as I
know them **[read, from memory of the reference]**. Each rule the design
depends on is confirmed by a run on the emulator, named beside it, and the
emulator is the executable reference here; where a rule is only read it says
so.

- **Registers.** 64 physical address registers (`XCHAL_NUM_AREGS 64`, ABI.md
  §7a). WINDOWBASE (SR 72) counts panes of four; `aN` is physical register
  `(4·WINDOWBASE + N) mod 64`. WINDOWSTART (SR 73) has one bit per pane,
  set where a live frame begins.
- **The overflow check.** With PS.WOE = 1 and PS.EXCM = 0, an instruction
  naming `aN`, N ≥ 4, raises a window overflow exception if a WINDOWSTART
  bit in panes WINDOWBASE+1 .. WINDOWBASE+⌊N/4⌋ is set. With EXCM = 1 or
  WOE = 0 there is no check. **[run]**: the spill fault's EPC1 is
  `addi a10, a2, -1`, an instruction naming a10; the trap entry names a4..a15
  of every window with EXCM = 1 and never overflows (every windowed case).
- **The overflow exception.** PS.OWB ← WINDOWBASE, WINDOWBASE ← the oldest
  live frame in reach (the pane whose bit was found), PS.EXCM ← 1, EPC1 ← the
  excepting instruction; the vector is WindowOverflow4, 8 or 12 by the
  distance from that frame to the next set bit, which is the call size the
  frame used. `rfwo`: WINDOWSTART[WINDOWBASE] ← 0, WINDOWBASE ← PS.OWB,
  EXCM ← 0, jump to EPC1, which re-executes the instruction.
- **`retw` and underflow.** n ← a0[31:30]. n = 0 is undefined (the emulator
  raises IllegalInstruction **[read, not run]**). WINDOWBASE ← WINDOWBASE −
  n; if WINDOWSTART[new WINDOWBASE] is clear, an underflow exception: PS.OWB
  ← the old WINDOWBASE, EXCM ← 1, EPC1 ← the `retw`, vector WindowUnderflow4n
  with WINDOWBASE at the caller. `rfwu`: WINDOWSTART[WINDOWBASE] ← 1,
  WINDOWBASE ← PS.OWB, EXCM ← 0, jump to EPC1, which re-executes `retw`.
- **`entry as, imm`.** Requires PS.WOE = 1 and EXCM = 0 (otherwise
  undefined; IllegalInstruction on the emulator **[run, §1]**). It writes
  `a(4·CALLINC + 1)` ← the caller's a1 − imm, then WINDOWBASE += CALLINC and
  sets that WINDOWSTART bit. `callN`/`callxN` set PS.CALLINC = N/4 and put
  the return address with N/4 in bits 31:30 in the caller's `aN`.
- **`movsp at, as`.** If WINDOWSTART bits WINDOWBASE−1 .. −3 are all clear
  (the caller is spilled), the Alloca exception, EXCCAUSE 5, through the
  general vector; otherwise a move. **[run: `alloca` mutant, cause 5 at the
  `movsp`]**.
- **`rotw imm`** adds imm (in panes) to WINDOWBASE; the next instruction sees
  the new window **[run: the trap path]**. **`wsr windowbase`** needs `rsync`
  before the registers are used **[read; `_start` already does it]**.
- **`s32e`/`l32e`** are word stores and loads with offsets −64..−4, for the
  window handlers; with no MMU rings they are plain accesses **[read]**.
- **The double exception.** An exception taken with PS.EXCM = 1 goes to the
  double vector (VECBASE + 0x3C0) with DEPC ← its PC, EXCCAUSE and EXCVADDR
  its own, and EPC1 and PS unchanged **[run: `rom_spill_fault` reports
  DEPC = VECBASE + 0x80, the overflow handler's first store, and EPC1 the
  instruction that raised the overflow]**.
- **Interrupts.** EXCM = 1 masks levels up to `XCHAL_EXCM_LEVEL` = 3
  **[run, ABI.md §5d]**; the tick is level 1.

**Where a spill goes.** Write sp_k for frame k's stack pointer after its
`entry`, frame k−1 its caller and k+1 its callee.

- Frame k's a0..a3 go to the **base save area** [sp_{k+1} − 16, sp_{k+1}),
  the 16 bytes below its callee's stack pointer. The windowed ABI reserves
  the 16 bytes below every live frame's stack pointer for this.
- A call8 frame's a4..a7, and a call12 frame's a4..a11, go to its **extra
  save area** [X − 32, X − 16) or [X − 48, X − 16), where X = sp_{k−1}, the
  caller's stack pointer, read from the caller's own base save area at
  sp_k − 12. That word is valid because overflow always spills the oldest
  frame first, so the caller was spilled before frame k. The compiler sizes
  `entry` to hold the extra save area at the top of the frame.

---

## 3. The handlers

All six live at their vectors (ABI.md §5a), run in the faulting thread's own
context with PS.EXCM = 1, touch only the thread's registers and its stack,
and enter no kernel code. Each is a fixed set of word accesses and one
return; none uses a scratch register the frame does not own. Written for this
kernel from the rules in §2. **[run]**: `windowed_process` overflows and
underflows at every size (chains of depth 40 at call4, call8, call12; 40
frames of four registers is 160 registers, past the 64); `of4`, `of8`, `of12`
(a handler storing two registers to each other's slots) are each red.

### 3.1 WindowOverflow4 (VECBASE + 0x000)

WINDOWBASE is the frame k to spill; its callee's window starts at a4, so
**a5 is sp_{k+1}**.

```
s32e a0, a5, -16    k's a0 -> base save area word 0
s32e a1, a5, -12    k's a1 (sp_k) -> word 1: the word k+1's spill reads
s32e a2, a5, -8
s32e a3, a5, -4
rfwo
```

Faults if [sp_{k+1} − 16, sp_{k+1}) is not writable.

### 3.2 WindowUnderflow4 (0x040)

WINDOWBASE is the caller k being filled; **a5 is the returning callee's
sp**.

```
l32e a0, a5, -16
l32e a1, a5, -12
l32e a2, a5, -8
l32e a3, a5, -4
rfwu
```

Faults if the callee's base save area is not readable.

### 3.3 WindowOverflow8 (0x080)

**a9 is sp_{k+1}**; a0 is free once stored and carries X.

```
s32e a0, a9, -16    base save area
l32e a0, a1, -12    a0 = X = sp_{k-1}, from k's own base save area
s32e a1, a9, -12
s32e a2, a9, -8
s32e a3, a9, -4
s32e a4, a0, -32    extra save area [X-32, X-16)
s32e a5, a0, -28
s32e a6, a0, -24
s32e a7, a0, -20
rfwo
```

Faults if the base save area is not writable, the word at sp_k − 12 is not
readable, or the extra save area is not writable.

### 3.4 WindowUnderflow8 (0x0C0)

```
l32e a1, a9, -12    sp_k first: the extra save area is found through it
l32e a0, a9, -16
l32e a7, a1, -12    a7 = X, from k's own base save area
l32e a2, a9, -8
l32e a3, a9, -4
l32e a4, a7, -32
l32e a5, a7, -28
l32e a6, a7, -24
l32e a7, a7, -20    X's carrier last
rfwu
```

### 3.5 WindowOverflow12 (0x100) and WindowUnderflow12 (0x140)

The same with **a13** as sp_{k+1}, the extra save area 32 bytes
[X − 48, X − 16) for a4..a11, and a11 as X's carrier in the underflow:

```
WindowOverflow12:               WindowUnderflow12:
s32e a0, a13, -16               l32e a1, a13, -12
l32e a0, a1, -12                l32e a0, a13, -16
s32e a1, a13, -12               l32e a11, a1, -12
s32e a2, a13, -8                l32e a2, a13, -8
s32e a3, a13, -4                l32e a3, a13, -4
s32e a4..a11, a0, -48..-20      l32e a4..a10, a11, -48..-24
rfwo                            l32e a11, a11, -20
                                rfwu
```

The largest handler is 14 instructions (42 bytes), inside its 64-byte slot.

### 3.6 Alloca (EXCCAUSE 5, through the user vector)

`movsp` found its caller spilled. The fix is to fill the caller as an
underflow would and retry the `movsp`; after that the caller is live and is
spilled below the new stack pointer when its turn comes. The user vector
(VECBASE + 0x340) now dispatches on the cause before the full save:

```
0x340: xsr  a1, excsave1        a1 = frame, EXCSAVE1 = user a1
       s32i a0, a1, F_A0        a0 is the one scratch
       rsr  a0, exccause
       bnei a0, 5, 1f
       j    alloca_exc
    1: j    trap_entry          a syscall, an interrupt, a fault
```

```
alloca_exc:                     a1 = frame, user a0 in F_A0
    s32i  a2, a1, F_A2          a2 scratch, in the frame
    rsr   a0, ps
    extui a2, a0, 8, 4
    slli  a2, a2, 8
    xor   a0, a0, a2            PS.OWB cleared
    rsr   a2, windowbase
    slli  a2, a2, 8
    or    a0, a0, a2            PS.OWB = this frame's WINDOWBASE
    wsr   a0, ps                (EXCM stays 1)
    rsync
    l32i  a2, a1, F_A0
    extui a2, a2, 30, 2         n = the caller's call size, in panes
    l32i  a0, a1, F_A0          user a0 back
    beqi  a2, 2, 8f
    beqi  a2, 3, 12f
    l32i  a2, a1, F_A2          n = 1, or 0: see below
    xsr   a1, excsave1          user a1 back, EXCSAVE1 = frame again
    rotw  -1                    to the caller
    j     _WindowUnderflow4     fills it; rfwu returns to the movsp
 8: (the same with rotw -2, j _WindowUnderflow8)
12: (the same with rotw -3, j _WindowUnderflow12)
```

After the rotation, the underflow handler's a(4n+1) is the faulting frame's
a1, the old stack pointer the caller was spilled below, which is what a fill
needs. The registers it loads are the caller's own panes WINDOWBASE−n ..
WINDOWBASE−1, which are dead: the caller is spilled, so every older frame is
too. `rfwu` sets the caller's WINDOWSTART bit, returns WINDOWBASE to PS.OWB
and re-executes the `movsp`, which now finds a live caller.

n = 0 occurs only in a thread's first frame, whose a0 is the 0 the start
state gives it (§5); that frame was entered as if by `call4`, so 0 is read as
1. Any other frame with n = 0 has a corrupt a0, and the fill reads that
process's own stack.

**[run]**: `windowed_process`'s alloca test calls `holder`, which holds four
values from a volatile-salted `mix` across a call to `alloca_after_spill`,
which runs a 20-deep call8 chain (spilling `holder` and itself), returns
(reloading itself only), then `alloca`s (clang emits `movsp a1, a8`), fills
the buffer, runs a 10-deep call12 chain (spilling again, now below the new
stack pointer), and checks the buffer; `holder` then checks its four values.
The `alloca` mutant (cause 5 sent to the ordinary trap path) ends the
process with `cause=0x00000005` at the `movsp`, so the exception is taken.

---

## 4. The trap entry and the restore

### 4.1 Save all 64; never touch the user's stack

The kernel saves all 64 physical registers into the frame. The alternative,
spilling the live windows to the user's stack as `xthal_window_spill` does,
was rejected:

- it makes the kernel store into user memory, through user stack pointers,
  so a bad pointer is a fault in kernel code, and on an isolated tier a
  permission question at every trap;
- it is a loop over a variable number of frames; saving 64 is straight-line
  and the same for every trap and every thread, call0 or windowed, so the
  kernel needs no idea of a thread's convention;
- a switch must still overwrite all 48 registers outside the incoming window
  or the outgoing thread's values stay readable (`rotw` is unprivileged), so
  spilling saves nothing on the way back in.

Saving all 64 restores every physical register on every resume, which is the
whole defence against one thread reading another's registers (§9).

### 4.2 The frame

```
F_PC 0   F_PS 4   F_A0..F_A15 8..68     (the current window, as today)
F_SAR 72   F_LBEG 76   F_LEND 80   F_LCOUNT 84
F_EXCCAUSE 88   F_EXCVADDR 92   F_SCOMPARE1 96   F_THREADPTR 100
F_WINDOWBASE 104   F_WINDOWSTART 108
F_AR 112..300    48 words: word k is physical AR[(4·WINDOWBASE + 16 + k)
                 mod 64], the panes WINDOWBASE+4 .. +15
FRAME_BYTES 304 (76 words)
```

The first 104 bytes are today's layout unchanged, so every accessor in
`lib.saw` (the syscall registers a2..a5 and a8, the PC, the cause) reads the
same words. The syscall registers are those of the window that executed
`syscall`, which is what a windowed stub uses too (§7). PS is saved whole,
so its window fields (WOE, CALLINC, OWB) ride in F_PS.
`THREAD_FRAME_STRIDE` = 512·⌈304/512⌉ = 512, as today; K2's 19 FP and BR
words would make 380, still 512 **[read, `kernel/core/limits.saw`]**.

### 4.3 The entry, from any window state

The trap arrives with PS.EXCM = 1, which turns the window overflow check off
(§2), and it stays 1 until the save is done. So no window exception can fire
whatever WINDOWBASE and WINDOWSTART the user left. Then the kernel's PS =
0x0F has WOE = 0, so none fires in the kernel either.

```
(vector, §3.6: xsr a1, excsave1; s32i a0, a1, F_A0; cause test)
trap_entry:
    s32i a2..a15, a1, F_A2..F_A15                       14
    rsr a2, excsave1; s32i a2, a1, F_A1                  2  user a1
    rsr/s32i epc1, ps, sar, lbeg, lend, lcount,
             exccause, excvaddr, scompare1              18
    rur a2, threadptr; s32i                              2
    rsr a2, windowbase;  s32i a2, a1, F_WINDOWBASE       2
    rsr a2, windowstart; s32i a2, a1, F_WINDOWSTART      2
    movi a2, 0; wsr a2, lcount                           2  as today
    mov  a13, a1        frame address into a13 (saved)
    rotw 3              a12..a15 become a0..a3: a13 is a1
    s32i a4..a15, a1, F_AR + 0..44      panes +4..+6    14
    mov a13, a1; rotw 3; s32i ..., F_AR + 48..92 (+7..+9)    14
    mov a13, a1; rotw 3; s32i ..., F_AR + 96..140 (+10..+12) 14
    mov a13, a1; rotw 3; s32i ..., F_AR + 144..188 (+13..+15) 14
    movi a2, 0x0F; wsr a2, ps; rsync    the kernel: WOE 0, EXCM 0,
                                        INTLEVEL 15             3
    mov a2, a1; l32i a3, F_EXCCAUSE; l32i a4, F_EXCVADDR
    l32r a1, kstack; l32r a0, ktrap; callx0 a0                  6
```

The kernel then runs on window WINDOWBASE + 12 (mod 16) with the user's
WINDOWSTART still in the register; with WOE = 0 neither matters, since
nothing in the kernel names a window **[read]**. The frame address rides
through each `rotw 3` in a13, a register already stored, because `rotw 3`
renames the old a12..a15 as a0..a3.

### 4.4 The restore (`sos_resume_frame`), the switch and the first entry

```
    mov a1, a2
    l32i/wsr sar, lbeg, lend, scompare1; l32i/wur threadptr; epc1
    l32i a2, F_PS; wsr a2, ps; isync    EXCM = 1 from here to rfe: no
                                        interrupt, no window check
    l32i a2, F_LCOUNT; wsr lcount; isync          as today
    l32i a2, F_WINDOWSTART; wsr a2, windowstart
    wsr  a1, excsave1                   the frame rides the rename here
    l32i a2, F_WINDOWBASE; addi a2, a2, 12; extui a2, a2, 0, 4
    wsr  a2, windowbase; rsync          to the frame's WINDOWBASE + 12
    rsr  a1, excsave1
    l32i a4..a15, a1, F_AR + 144..      panes +13..+15
    rotw -3; mov a1, a13                old a1 is now a13; a1 is a pane
                                        not yet loaded
    l32i a4..a15, F_AR + 96..           +10..+12
    rotw -3; mov a1, a13; l32i ..., F_AR + 48..     +7..+9
    rotw -3; mov a1, a13; l32i ..., F_AR + 0..      +4..+6
    rotw -3; mov a1, a13                the frame's own window
    l32i a0, a2..a15 from F_A*
    l32i a1, a1, F_A1                   EXCSAVE1 already holds the frame
    rfe
```

Loading the groups from the top down is what lets the carrier move: after
each `rotw -3` the old a1 is a13 of a group not yet loaded, and is copied to
a1 (a register of the next group down, or of the frame's own window) before
the group's loads overwrite a13. This is the whole context switch and the
first entry to user mode, as today: `ktrap` returns a frame, any frame.

### 4.5 Cost

Counted on the assembled code, straight-line paths **[run: `count.sh`,
`count_path.py`; the entry figure counted by hand from the source because
objdump mis-decodes the padded vector table]**:

```
                      main    prototype
vector to ktrap        50       112
resume to rfe          37       101
round trip             87       213     +126 instructions per trap
.text.vectors (IRAM)  1303 B    1795 B  +492 B of IRAM's 16 KiB
sos_windowed_call       -       126 B  flash
frame                 104 B     304 B  per thread; stride 512 either way
```

Every added instruction is a `s32i`, `l32i`, `rotw`, `mov` or SR access in
IRAM against the kernel's DRAM; none branches. A window exception costs its
handler, 5 to 14 instructions, and no kernel entry; an Alloca costs about 25
plus an underflow handler.

---

## 5. Thread start

`frame_init` zeroes all 76 words, then writes:

```
F_PC          entry
F_PS          0x0005_0030   UM, EXCM (rfe clears it), INTLEVEL 0,
                            WOE = 1, CALLINC = 1, OWB 0
F_A1          stack_top
F_A2, F_A6    arg0
F_WINDOWBASE  0
F_WINDOWSTART 1
```

- **A call0 entry** finds arg0 in a2, the stack in a1 and a0 = 0, as today.
  WINDOWSTART names only its own pane, so no reference it makes can find a
  set bit in panes +1..+3: it never overflows, and it executes no `entry`,
  `callN` or `retw`, so WOE and CALLINC are never read. a6 is a call0
  argument register no one-argument entry reads. **[run]**: the full smoke
  on the prototype (every existing case is call0, `frame_hold` and
  `thread_preempt` among them) passes with identical consoles; a call0 C
  root reads `ps=0x00050020 wb=0x00000000 ws=0x00000001` at `_start`
  (`new_call0_state.console`).
- **A windowed entry** opens with `entry a1, N`. CALLINC = 1 makes it a
  `call4`ed function: its stack pointer is stack_top − N, WINDOWBASE becomes
  1, WINDOWSTART 3, its a0 is the old a4 = 0 and its a2 the old a6 = arg0.
  Pane 0 is a fake caller: a live frame (its WINDOWSTART bit) that the
  thread never returns to (its own `retw` has n = 0, an IllegalInstruction
  **[read]**, the windowed form of today's "a returning entry faults"), and
  that is spilled first when the register file wraps, into the 16 bytes
  below the first frame's stack pointer, which the windowed ABI reserves.
  That spill writes stack_top into the first frame's base save area at
  sp − 12, which is what the first frame's own call8 or call12 spill reads
  to find its extra save area, [stack_top − 32 or − 48, stack_top − 16),
  inside its own `entry` allocation. Nothing is written at or above
  stack_top. **[run]**: `windowed_process` reads
  `ps=0x00050020 wb=0x00000001 ws=0x00000003` at `_start`, and the `a6`
  mutant (arg0 in a2 only) and the `callinc` mutant (CALLINC 0) are red;
  with CALLINC 0 the first frame's call8 spill reads the zeroed word at
  sp − 12 and faults storing at 0xFFFFFFE0, which is exactly the hazard the
  fake caller removes.

**Why CALLINC 0 is not an option.** With CALLINC 0 `entry` does not rotate
and the first frame has no caller, so the word its call8 or call12 spill
reads at sp − 12 is whatever the stack held; the kernel cannot put a value
there because it does not know N. A process would have to declare its
convention or its first frame's size. CALLINC 1 needs neither.

---

## 6. The double exception

### 6.1 The rule

A double exception whose DEPC lies in [VECBASE, VECBASE + 0x180) (the six
window handlers) and whose PS.UM is 1 is the running process's fault on its
own stack: a spill to an address that does not take the store, or a fill
from one that does not take the load. Anything else, including the same
fault with PS.UM = 0 (the kernel's own windowed call, §7), is a kernel bug.

```
0x3C0: wsr  a0, excsave2        two scratch registers; no level-2 or -3
       wsr  a2, excsave3        interrupt is used, so their EXCSAVEs are free
       j    double_entry
double_entry:
       rsr  a0, depc
       l32r a2, =_vector_base
       sub  a0, a0, a2
       movi a2, 0x180
       bgeu a0, a2, 1f          not a window handler
       rsr  a0, ps
       bbci a0, 5, 1f           PS.UM clear: the kernel's
       rsr  a0, excsave2
       rsr  a2, excsave3
       xsr  a1, excsave1        a1 = the running thread's frame
       s32i a0, a1, F_A0
       j    trap_entry          an ordinary user trap
    1: movi a2, 0x3C0
       j    vec_fatal           kernel_fault: the machine stops
```

EXCSAVE1 is a frame only while user context runs; the UM test is what makes
it safe to use. **[run]**: with the test removed (`dbl_um`), the kernel's own
spill fault in `rom_spill_fault` took EXCSAVE1 (0, the boot value) as a
frame and its first store faulted at address 8; on the emulator that stops
the machine, but a stale EXCSAVE1 naming memory would have been written.

### 6.2 What the process is told, and the thread's state

The trap entry saves the frame as for any trap, with EXCCAUSE and EXCVADDR
those of the failed access and EPC1 still the user instruction whose window
exception it was (the double exception does not touch EPC1). `ktrap` sees a
fault and `fault_trap` ends the process with the reason the spec gives a bad
access, by the access's own cause:

```
spill below 0x10000010 (unmapped):
SOS: fault store-access-fault cause=0x0000001d epc=0x421800e6 tval=0x10000000
  (epc: `addi a10, a2, -1`, the reference that raised the overflow;
   tval: the base save area's first word)
fill from below 0x10000010:
SOS: fault load-access-fault cause=0x0000001c epc=0x421800fc tval=0x10000004
  (epc: the `retw`; tval: UnderflowN's first load, sp - 12)
```

**[run: `windowed_spill_fault`, `windowed_fill_fault`]**: a windowed C child
of a call0 launcher (`death-notify`'s root) faults; the teardown line, the
launcher's wake with the status word and its `done` follow, and the machine
ends in `halt pass`.

The dead thread's frame holds the window of the frame being spilled or
filled, with WINDOWBASE rotated to it and the handler's partial work (an
overflow8 or 12 has replaced a0 with the extra save pointer). It is not a
resumable state and is never resumed: the process ends.

---

## 7. The kernel calling a windowed function

### 7.1 The stub

`u32 sos_windowed_call(u32 fn, u32 a, u32 b, u32 c, u32 d)`, a call0
function in flash (`.text.sos_windowed_call`):

```
addi a1, a1, -64
s32i a0, a12, a13, a14, a15 -> 0..16     call0's callee-saved and a0
rsr  a7, ps;          s32i -> 20
rsr  a7, windowstart; s32i -> 24
addi a7, a1, 64; addi a8, a1, -16; s32i a7, a8, 4
                     the word at a1 - 12 is this frame's top: an overflow
                     of this frame finds its extra save area at words
                     32..47 of its own 64 bytes
rsr  a7, windowbase; ssl a7; movi a8, 1; sll a8, a8
wsr  a8, windowstart          this frame is the only live window
movi a8, 1; slli a8, a8, 18; addi a8, a8, 0x0F
wsr  a8, ps; rsync            WOE 1, EXCM 0, UM 0, INTLEVEL 15
mov  a10..a13, a3..a6         the callee's a2..a5
callx8 a2
mov  a2, a10                  the callee's a2
l32i a7 -> wsr ps; rsync      back to PS 0x0F
l32i a7 -> wsr windowstart; rsync
l32i a0, a12..a15; addi a1, a1, 64; ret
```

- **Masking**: INTLEVEL stays 15 throughout, so no interrupt is taken during
  the ROM call; the stub writes PS only to set WOE and back **[run]**.
- **Window state**: a call from a call0 frame needs WOE = 1 and EXCM = 0 for
  `entry`, and a WINDOWSTART with only the caller's bit, because the
  kernel's WINDOWSTART after a trap is the user's (§4.3) and a stale bit
  would make the callee's first reference spill a frame that does not
  exist. **[run]**: `rom_call` sets every WINDOWSTART bit, then calls the
  ROM again, and gets the same CRC.
- **Registers**: `callx8` makes the callee's window this frame's a8..a23, so
  the callee clobbers a8..a15; a12..a15 are call0 callee-saved and are
  restored. The 48 registers outside the kernel's window are clobbered, and
  the kernel keeps nothing there; the next resume reloads all 64.
- **Stack**: the ROM function runs on the kernel stack below the stub's 64
  bytes. The ROM's own `.stack_pro` is not used.
- **The return**: `retw` makes PC[31:30] the caller's, so the stub must sit
  at an address whose top bits are 01, which flash (0x42..) and IRAM
  (0x403..) both are.

### 7.2 An overflow under the stub

The stub's frame is a live window with a valid a1 − 12 word, so the same
vectors spill and fill it correctly if a ROM function nests deep enough: they
need only that the kernel stack below the stub is mapped, which it is.
**[run]**: `rom_call` calls a 24-deep call8 chain from the kernel through the
stub and gets `deep=0x00181000`, the chain's checked answer; the `stub12`
mutant (no a1 − 12 word) stops the kernel at the double vector with
EXCVADDR 0xFFFFFFE1. A fault there is the kernel's (§6.1), and
`rom_spill_fault` shows it: `SOS-S3: kernel fault vector=0x000003c0
exccause=0x0000001d ... excvaddr=0x10000000 depc=0x40378480`.

The stub does not also need to guarantee no overflow, but the PLL's calls
cannot overflow anyway. From the ROM ELF's disassembly and the initial phy
table **[read; the table's runtime values run, `rom_call`]**:

```
rom_i2c_writeReg (0x4003_5880, entry 32)
  -> rom_chip_i2c_writeReg (0x4003_5818, entry 32)       [phy +0x18C]
       -> rom_enter_critical_phy (0x4005_5BB8)            [+0x160] leaf
       -> rom_get_i2c_hostid (0x4003_54BC)                [+0x15C] leaf
       -> rom_exit_critical_phy (0x4005_5BC0)             [+0x164] leaf
rom_i2c_writeReg_Mask (0x4003_58D8, entry 48)
  -> rom_i2c_readReg (0x4003_57F8)                        [+0x188]
       -> rom_chip_i2c_readReg (0x4003_5548)              [+0x16C]
            -> enter_critical, rom_get_i2c_read_mask (0x4003_5474,
               +0x154), hostid, rom_chip_i2c_readReg_org (0x4003_54FC,
               +0x168), exit_critical                     leaves
  -> rom_i2c_writeReg (as above)                          [+0x190]
```

At most four frames below the stub, every call `callx8`; six fit before one
overflows. None of the ten functions executes `rsil`, `wsr ps`, `xsr ps` or
`waiti` **[read]**. They poll the analog I2C master's busy bit
(`bany` on a word at 0x6000_E0xx) and write it; what the emulator does there
was not run. `rom_enter_critical_phy` returns 0 and does nothing.

### 7.3 What the ROM functions touch, for SO-113

- **The phy table** (`rom_phyFuns` at 0x3FCE_F3D4, pointing at the table
  that follows it): read on every call. It is in the RAM pool processes may
  be given (§0). `rom_call` read it at boot as the ELF initializes it.
- **The analog I2C master** at 0x6000_E040..0x6000_E048 and the I2C
  registers the ROM computes from the host id **[read]**.
- **The kernel stack** for its frames, up to about 4 × 48 bytes plus the
  stub's 64.

---

## 8. A windowed process: what it links

- **The system-call stub**: `hal/xtensa-esp32s3/user/syscall.c` unchanged,
  compiled windowed. Its register variables put the handle in a2, the
  arguments in a3..a5 and the op in a8 of the stub's own window after
  `entry` (`entry a1, 32; mov a8, a3; mov a3, a4; syscall; retw`), which is
  the registers the frame's F_A2..F_A8 hold, so the kernel side is unchanged
  **[run: disassembly; `windowed_process`'s system calls from depth 30]**.
- **`rt/common_c/support.c`** compiles windowed with Blade's flags (seven
  functions, each opening with `entry`) **[run: compiled standalone]**; no
  windowed case links it yet, because no case has Saw in it.
- **Blade's `target-features = "+windowed"`** in
  `[sos.xtensa-esp-unknown-elf]`: Blade passes the triple's
  `-windowed,-fp,-loop,-mac16,-bool` and then the manifest's `+windowed` to
  each C unit, last wins, and `--target-features +windowed` to sawc **[read,
  `blade/src/sosimg.saw` `native_compile_arguments`, `builder.saw`]**. A
  package so built runs windowed: `_start` reads WINDOWBASE 1 after its
  `entry` **[run]**. The packages here are all C with an empty `main.saw`
  (the `c-child` pattern), so no Saw is compiled windowed into them
  (rule 3.1 of the networking design stands).
- **Toolchain behaviour found on the way [run]**: in a `.S` file the
  integrated assembler accepts `entry`, `callN`, `retw`, `rotw`, `s32e`,
  `rfwo` and `rsr windowbase` under the call0 flags; in a C unit's inline
  assembly under `-windowed` it refuses the windowed instructions ("requires
  an option to be enabled") and the names `windowbase` and `windowstart`, so
  a call0 C unit that must read them encodes `rsr` by hand, as `hold.c`
  already does for the loop registers.

**What windowed Saw will need once sawlang's W0 lands**: a case package with
real Saw built `+windowed` whole (its `sosrt` and the panic path included),
linking the windowed `syscall.c` and `support.c`; a Saw analogue of
`windowed_process` (deep recursion holding values through overflow and
underflow, a system call from depth, two threads preempted deep); and a
windowed Saw child beside a call0 Saw root. The kernel side needs nothing
more.

---

## 9. Interrupts and the tick

- **During a window handler or the Alloca path**: impossible. Both run with
  PS.EXCM = 1, which masks level 1 (ABI.md §5d); a tick due then is taken at
  the instruction `rfwo`/`rfwu` returns to, which is the faulting one,
  re-executed, from a consistent window state. The kernel enables no
  interrupt above level 1, so nothing preempts a handler. **[read; the
  thread rounds take thousands of window exceptions with the tick on and
  pass, which is consistent with it but does not force the case.]**
- **During the trap entry or the restore**: EXCM = 1 or INTLEVEL 15 throughout
  **[read, as today]**.
- **Between a `call8` and the callee's `entry`**: PS.CALLINC is set, the
  window is not rotated, the caller's a8 holds the return address and a9 is
  free. A trap there saves PS whole, CALLINC included, and all 64 registers;
  the resume restores both and `entry` runs as if nothing happened.
  **[run]**: in the last two thread rounds one thread spins *before* its
  callee's `entry` (after `call8` in one round, `call12` in the other),
  having published that it is there, and the other thread releases it only
  after seeing that, so the spinner is preempted at that point whichever
  thread runs first. The `callinc_restore` mutant (CALLINC forced to 1 on
  resume) is red in both rounds.
- **Deep in a chain**: all 64 registers and WINDOWBASE/WINDOWSTART are in the
  frame. **[run]**: two threads, each 24 frames deep in a call4, call8 or
  call12 chain (all registers live), spin at the bottom until the other has
  published its round, so every round crosses between them by a tick; the
  `save48`, `load48`, `ws` and `wb` mutants are red.
- **During the ROM stub**: INTLEVEL 15.

---

## 10. The cases

All in the S3 smoke; all run on the emulator except where marked. The
fixture packages are C (`tests/xtensa-windowed*`), plus kernel tests in
`tests/xtensa/`.

```
case                  holds                                 forced by
windowed_process      start state (ps/wb/ws asserted);      depth 40 > 64/N
                      overflow+underflow at call4/8/12;     registers;
                      syscall from depth 30 through the     a call before the
                      windowed stub, status and value       alloca that spills
                      checked; movsp with caller spilled;   its caller; spins
                      two windowed threads switched deep    that end only
                      in chains; a tick between callN and   when the other
                      entry (call8 and call12)              thread has run
windowed_spill_fault  a spill to an unmapped address ends   sp set by hand,
                      only that process, with the store's   then a chain that
                      reason; its launcher goes on          wraps the file
windowed_fill_fault   the same for a fill (the load's)      spill, return,
                                                            sp by hand, retw
rom_call              the kernel calls the ROM's crc32_le   a 24-deep chain
                      ("123456789" -> 0xcbf43926), a        from the kernel;
                      windowed chain deep enough to         WINDOWSTART set
                      overflow from the kernel, and the     to all ones
                      ROM again after a dirty WINDOWSTART
rom_spill_fault       a spill fault under the kernel's      sp set by hand
                      call stops the machine at 0x3C0
every existing case   call0 unchanged: consoles identical   -
                      to main's but for host time and
                      code addresses
```

`windowed_process` has `tick` on, which the thread rounds need. The mixed
case the issue asks for (a windowed and a call0 process side by side) is the
two fault cases: a call0 Saw launcher and a windowed C child. A case with a
call0 and a windowed process both running to completion is cheap to add
(`death-notify`'s root launching a windowed child that exits) and is the
first thing the build should add (U2).

**Mutants, each run [run: `mutants.txt`]**:

```
mutant           change                                result
save48           a9 of each 3-pane group not saved     red (fault at start)
load48           ... not loaded                        red
ws               WINDOWSTART not restored              red (thread rounds)
wb               WINDOWBASE restored as 0              red
of4              overflow4 swaps a2/a3 slots           red (chain4)
of8              overflow8 swaps a5/a6 extra slots     red (chain8)
of12             overflow12 swaps a9/a10 extra slots   red (chain12, alloca)
alloca           cause 5 sent to the trap path         red (cause 5 at movsp)
a6               arg0 only in a2                       red
callinc          start PS with CALLINC 0               red (spill at
                                                       0xFFFFFFE0)
callinc_restore  CALLINC forced to 1 on resume         red (pre-entry rounds)
stub12           stub writes no a1 - 12 word           red (rom_call)
dbl_um           double path ignores PS.UM             red (rom_spill_fault,
                                                       once DEPC and EXCVADDR
                                                       are asserted)
```

`of4` survived a first version of the chains whose answer was symmetric in
a2 and a3; the chains now answer seed + (depth << 16), and it is red. The
underflow handlers have no mutant of their own: a mutant that swaps two
slots in an overflow handler is the same defect seen from the other side,
and the fills are checked by every chain's values on the way back out.

**Board stage** (not runnable here): every case above on the part, and the
cost (§12).

---

## 11. What I ran

The prototype (`prototype.diff`, applies to the worktree's base
`6a6e33d7`): `boot.S` (handlers, Alloca, double path, 64-register entry and
restore, `sos_windowed_call`), `lib.saw` (frame, start state), the five
cases above in `tools/sos_runner.py`, the fixture packages, and an `xtensa`
section for `tests/death-notify`. Scripts and logs are beside this file
(`smoke.sh`, `boot.sh`, `mutant.py`, `runmutants.sh`, `count.sh`,
`trapcount.sh`, `*.log`, `*.console`, `transcripts/`).

- The whole S3 smoke on the prototype: 46 passed, the board-only case not
  run (`full2.log`).
- Consoles of the 39 existing emulator cases on main's kernel and on the
  prototype (`transcripts/base`, `transcripts/proto`, `diff.txt`): the only
  differences are host-time values (`mhz=`, `thread_preempt`'s tick lines,
  letter interleaving and `interrupts=`, all printed-only by CLAUDE.md's
  rule) and code addresses that moved by the stub's size (`trap_fault`'s
  EPC1, the timer cases' tick PCs).
- The "today" probes (§1) on main's kernel; the call0 start state on the
  prototype.
- The mutants (§10).
- The instruction counts (§4.5).
- `kill_churn` under QEMU's exception log: 4788 user-vector exceptions and 51
  interrupts over 200 rounds, about 24 traps per round (the interrupt count
  is host time and not meaningful).
- `kill_churn_profile` board images built at `rom` (sha256
  `bb9f6566fd2d26fd5a175bbb134c41da660aad8032cd317a7c15f07fb1e8b9ec`) and
  `xtal40` (`3afb1216df148bfe707cadf1060e34a781ef400806d6e22c6d463e7806d4e55a`);
  not flashed, and deleted afterwards (a little over 2 MiB each; `--emit-flash
  kill_churn_profile --clock KEY` rebuilds them from the diff).

Not run: `make sos-test` (the change is the S3 HAL and the S3 part of the
runner; `death-notify`'s new section is for the S3 triple only); a `retw`
with a0 = 0; any FP in a windowed process; windowed Saw; the PLL's ROM
functions; anything on the part.

**What the emulator does not model, for the board stage**:

- window exception, `entry` and `retw` behaviour where the ISA says
  "undefined" (WOE = 0, n = 0): the emulator raises IllegalInstruction;
  the part may not;
- EXCSAVE2 and EXCSAVE3 on the part (used by the double path; present on the
  emulator, run; ESP-IDF's S3 port uses EXCSAVE_2..7 **[read]**);
- cycles: the emulator's clock is host time, so every cost here is in
  instructions;
- that the ROM initializes `.data_phyrom` before direct boot on a real
  power-on (it has on the emulator);
- the analog I2C master the PLL path writes.

---

## 12. What it costs every thread

- Frame 104 → 304 bytes per thread; `THREAD_FRAME_STRIDE` stays 512, so the
  kernel's frame arena does not grow.
- +126 instructions per trap, switch and first entry, straight-line in
  IRAM; +492 bytes of IRAM, +126 bytes of flash.
- `kill_churn` should lose very little. At about 24 traps per round, 200
  rounds is about 4,840 traps, so about 610,000 more instructions. At one to
  three cycles each that is 30 to 90 ms at `rom` (20 MHz) against the 26.05 s
  ABI.md §14h measured, 0.1 to 0.35%, and half that time at `xtal40`
  (13.44 s). The tick (`TICK_PERIOD_US` 10,000) adds about 100 traps a
  second of run on the part, another 0.06 to 0.2% at `rom`. This is an
  estimate from instruction counts; the board images of §11 measure it
  against SO-110's numbers.

---

## 13. An attack on this design

**Reaching kernel state.** The trap entry stores only through EXCSAVE1 into
the frame, and its correctness does not depend on WINDOWBASE, WINDOWSTART or
any PS window field the process left: it rotates relative to wherever it is.
A process can execute `rotw`, `wsr windowbase/windowstart`, `rfwo`, `rfwu`
and `s32e` itself (no privilege, ABI.md §5e), and each changes only its own
registers or stores through its own registers, as any store already can on
the Flat tier. A process that writes EXCSAVE1 makes its next trap store
anywhere; that hole is today's (§5e) and not widened. **[read]**

**Reading another process's registers.** Every resume loads all 64 physical
registers, WINDOWBASE and WINDOWSTART from the incoming frame, and
`frame_init` zeroes all 76 words, so no register of another thread, of the
kernel's (which runs in panes the restore reloads) or of a ROM call reaches a
thread. Two special registers do: after a double path, EXCSAVE2 and EXCSAVE3
hold the faulting window's a0 and a2, and any later process can `rsr` them.
On the Flat tier memory is readable anyway, but the fix is three
instructions (zero both once a0 is in the frame) and the build should take it
(question 5). EXCSAVE1 holds the running frame's address while user code
runs, as today.

**Holding off the kernel.** Window handlers and the Alloca path run with
interrupts masked for at most about 30 instructions and always end in
`rfwo`/`rfwu`; a process cannot lengthen them, and a stream of them leaves
the tick a window between each. `rsil` and a PS write remain today's holes.

**Crashing the kernel.** The only new way into kernel code with user state
is the double path, and it trusts EXCSAVE1 only when DEPC is in the window
handlers and PS.UM = 1, a state only user context produces (the `dbl_um`
mutant shows what the UM test prevents). A process can jump into a window
handler itself; the handler stores through the process's own registers and
returns to EPC1, or faults, and the fault is that process's. A process can
leave PS.UM clear before a fault, which sends its traps to the kernel vector
and stops the machine: today's hole.

**The ROM stub corrupting the kernel.** It runs with interrupts masked on the
kernel stack; the PLL path's ROM functions write no PS and nest four frames;
an overflow is handled in kernel context and its fault stops the machine.
The real risk is data: the ROM reads its function table from RAM a process
may own. Called after the loader, a ROM function could jump through a word a
process wrote, which is kernel code execution chosen by a process. The rule
is "ROM only before the loader", enforced (question 4).

**What I could not check.** Anything on the part; the ISA text itself (§2);
an interrupt arriving inside a window handler (masked by construction, not
forced); `retw` with n = 0 beyond the emulator's model; whether the part's
`entry` traps with WOE = 0; windowed code with FP enabled (K2).

---

## 14. Units of work

```
unit  size  what                                      needs     gate
U1    M     the kernel: boot.S handlers, Alloca,      -         emulator
            double path (with EXCSAVE2/3 zeroed),
            64-register entry and restore; lib.saw
            frame and start state; ABI.md §2b, §5a,
            §7c, §7d (rewritten as built), §7f (the
            stub as a PS writer); the spec sentence
            (§15). Lands with U2's cases, which gate it.
U2    S     the fixture packages and cases:           U1        emulator
            windowed_process, windowed_spill_fault,
            windowed_fill_fault, plus a windowed child
            that exits beside a call0 root; the
            mutants recorded in the cases' comments.
U3    S     sos_windowed_call, rom_call,              U1        emulator
            rom_spill_fault; the "ROM only before
            the loader" check.
U4    S     board stage: every windowed case and      U1-U3     board
            kill_churn_profile at rom and xtal40 on
            the part, read against SO-110.
U5    S     windowed Saw cases.                       W0, U2    emulator
```

U1 and U2 are one patch in practice (the cases are U1's gate); U3 can be the
same patch or the first part of SO-113.

---

## 15. Questions for the owner

1. **Save all 64 registers on every trap, or spill live windows to the
   user's stack?** Recommend all 64 (built, §4.1): straight-line, no user
   memory touched by the kernel, and the only way the restore clears every
   register anyway.
2. **One start state for both conventions (CALLINC 1, arg0 in a2 and a6),
   with no declaration in the image?** Recommend yes (§5): it ran for every
   existing call0 case and the windowed ones.
3. **Report a fault inside a spill or fill with the access's own cause and
   the user instruction as the PC** (`store-access-fault`, `epc` the
   reference that overflowed), or as a distinct reason? Recommend the
   access's own: it is the bad access the spec already names, and the PC is
   the thread's.
4. **The ROM's RAM.** The phy table the PLL's ROM calls read is in the RAM
   pool processes may be given. Recommend: the kernel calls the ROM only
   before the loader (in `clock_init`), with a kernel check that refuses a
   ROM call once a process has been loaded, and the pool stays where it is.
   Two alternatives: end the pool below the table (0x3FCE_F000, 4 KiB less
   pool), which protects this path's table and nothing else of the ROM's
   RAM; or keep every process range out of the ROM's RAM (0x3FCD_7E04 up,
   which also takes the top of the child region and its stack), about
   96 KiB of the 256 KiB process range.
5. **Zero EXCSAVE2/3 after the double path?** Recommend yes: three
   instructions, no reader of another process's registers left.
6. **What a windowed entry that returns should be.** Today a call0 entry
   that returns jumps to 0 and faults; a windowed one executes `retw` with
   n = 0, undefined in the ISA and an IllegalInstruction on the emulator.
   Recommend accepting that as the windowed form of the same rule, with a
   case (cheap) so the part's behaviour is seen at the board stage.

---

## 16. `spec.md`

One sentence changes; nothing new about windows belongs in the arch-free
text.

- §5b's ESP32-S3 paragraph says "(Xtensa, call0, every image)" and "A 26-word
  frame saves PC, PS, `a0`-`a15`, `SAR`, the loop registers, `SCOMPARE1` and
  `THREADPTR`". It becomes: Xtensa, call0 kernel, call0 or windowed images;
  a 76-word frame saves PC, PS, the whole 64-register file (the current
  window and the other 48 by their position from WINDOWBASE), WINDOWBASE,
  WINDOWSTART, `SAR`, the loop registers, `SCOMPARE1` and `THREADPTR`; and
  the window spill and fill handlers run in the thread's own context without
  entering the kernel.
- §5.7's "On Xtensa the run is call0's argument registers, which begin at
  `a2`" stays true for a windowed stub (its own a2.. after `entry`); a clause
  saying so is optional.
- A fault inside a spill is a bad access to the process's own stack, which
  §2.7 and §8's fault rule already cover in arch-free terms. How the kernel
  tells it from its own fault (DEPC, PS.UM) is ABI.md's, §5 and §7.
