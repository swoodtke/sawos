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

**Revision 2.** What changed from revision 1, which the reviewer read:

- The owner's rulings are in (§15): all 64 registers saved and restored on
  every trap, never a spill to a user's stack; one start state, nothing
  declared in the image; a fault in a spill or fill reported with the
  access's own cause; the kernel calls the ROM only before the loader,
  checked at the stub; a windowed entry that returns faults by a return
  address that faults on fetch. The lazy save of §12a is the alternative
  considered, deferred to SO-115 with lazy FP state.
- §2 gains the reviewer's two `retw` rules (a size that disagrees with the
  nearest live frame behind is illegal; a `retw` that does not underflow
  clears its frame's WINDOWSTART bit), and says what the rules were checked
  against.
- §5: the start state gains a4 = 0x7FFF_FFFC, the windowed entry's return
  address; and an entry point that does not open with `entry` must make only
  `call4` calls from its first frame (ABI.md §7d says so).
- §3.6: the Alloca path is the ROM's and ESP-IDF's shape, rotating into the
  spilled caller's dead pane for scratch; it serves the user vector and,
  since the stub, the kernel vector.
- §6: the double path parks nothing in EXCSAVE2 or EXCSAVE3: neither path
  resumes the interrupted context, so a0 and a2 are scratch and nothing of a
  faulting window is left where a later thread could read it.
- §7: the stub refuses a call once the loader has begun (`hal.payload_start`
  closes it); its a12 restore is held by a case, its WINDOWSTART restore has
  an observer, and an Alloca exception under it is handled at the kernel
  vector rather than stopping the machine.
- §10: the cases as built (fourteen new, the reviewer's asks among them)
  and every new line of the trap path, the handlers and the stub under a
  mutant; §11a records what the build ran; §4.5 the costs as built.
- §2b of ABI.md: `_start` writes PS (WOE = 0) before WINDOWSTART and
  WINDOWBASE, and through a2, so it no longer relies on the ROM's handover
  window having no WINDOWSTART bit in reach of `a9`.

The approach is sound on the emulator, and nothing found needs the scope
changed. One start state serves both conventions: PS.WOE = 1, PS.CALLINC = 1,
WINDOWBASE 0, WINDOWSTART 1, the entry argument in both `a2` and `a6`, the
stack top in `a1`, a zero `a0` (a call0 entry's return address) and
0x7FFF_FFFC in `a4` (a windowed entry's, which faults on fetch). A call0
entry never rotates the window, and a windowed entry's `entry` behaves as if
the thread had been `call4`ed by a frame that can be spilled into the
thread's own stack and is never returned into. No process that opens with
`entry` (or keeps to `call4` from its first frame) declares its convention.
**[run]**: the whole S3 smoke passed on the prototype and passes on the
build, every existing case's console is identical to main's apart from
host-time values and moved code addresses, and the new windowed cases pass
with every mutant red but the few §10 names (§8, §10, §11, §11a).

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

§12a is the lazy alternative (save the other 48 only at a switch),
evaluated by reading and by assembled instruction counts, not by a run, with
a recommendation and the units under either choice. The owner chose the
eager save; §12a stays as the starting point for SO-115.

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
so. The reviewer checked them against three implementations instead of the
manual: the six handlers of §3 are the mask ROM's own at 0x4000_0000
instruction for instruction (`esp32s3_rev0_rom.elf`), the Alloca path is the
ROM's and ESP-IDF v5.4.1's `_xt_alloca_exc`, the start state of §5 is
ESP-IDF's for a windowed task plus a2 (`port.c`), and the overflow vector's
choice is Espressif QEMU's `window_check`.

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
- **A `retw` whose size disagrees with the nearest live frame behind it is
  illegal.** With m the distance from WINDOWBASE back to the nearest set
  WINDOWSTART bit one to three panes back (0 when there is none), the
  emulator raises IllegalInstruction when n = 0 or when m ≠ 0 and m ≠ n
  (Espressif QEMU, `target/xtensa/win_helper.c`, `test_ill_retw`, read by
  the reviewer). Nothing here relies on it going the other way; a process
  with a corrupt a0 reaches it, and the IllegalInstruction is that
  process's.
- **A `retw` that does not underflow clears the returning frame's
  WINDOWSTART bit.** `movsp`'s test and the stub's one-bit WINDOWSTART both
  rest on WINDOWSTART naming live frames only. **[run, by the reviewer on
  the prototype: WINDOWSTART read at one depth before and after a call two
  frames deep has returned is the same, `0x0000000b`]**.
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
       j    user_alloca
    1: j    trap_entry          a syscall, an interrupt, a fault
```

```
user_alloca:                    a1 = frame, user a0 in F_A0
    rsr   a0, windowbase
    s32i  a0, a1, F_WINDOWBASE  the faulting window's WINDOWBASE, in a
                                frame word the next trap rewrites
    mov   a0, a1                the frame's address rides the rotation
    xsr   a1, excsave1          user a1 back, EXCSAVE1 = frame again
    rotw  -1                    one pane back: the caller's, spilled, so
                                a0..a3 are dead and free; the faulting
                                window's a0..a3 are a4..a7
    l32i  a3, a4, F_WINDOWBASE
    l32i  a4, a4, F_A0          the faulting window's a0 back
alloca_fill:
    rsr   a2, ps
    extui a1, a2, 8, 4; slli a1, a1, 8
    xor   a2, a2, a1            PS.OWB cleared
    slli  a3, a3, 8; or a2, a2, a3
    wsr   a2, ps; rsync         PS.OWB = the faulting window (EXCM stays 1)
    bbsi  a4, 31, 1f
    j     _WindowUnderflow4     n = 1, or 0: see below
 1: rotw  -1                    the faulting window's a0 is a8 now
    bbsi  a8, 30, 2f
    j     _WindowUnderflow8
 2: rotw  -1
    j     _WindowUnderflow12
```

This is the mask ROM's and ESP-IDF's shape (`_xt_alloca_exc`): the scratch
is the dead pane, so no register of the faulting window is stored anywhere,
and the size test is a0's bit 31, then bit 30. The kernel vector's Alloca
(§7.2) enters `alloca_fill` the same way, with the faulting window's a0
parked in EXCSAVE1 (free in the kernel) instead of the frame and its
WINDOWBASE read before the rotation.

After the rotation, the underflow handler's a(4n+1) is the faulting frame's
a1, the old stack pointer the caller was spilled below, which is what a fill
needs. The registers it loads are the caller's own panes WINDOWBASE−n ..
WINDOWBASE−1, which are dead: the caller is spilled, so every older frame is
too. `rfwu` sets the caller's WINDOWSTART bit, returns WINDOWBASE to PS.OWB
and re-executes the `movsp`, which now finds a live caller.

n = 0 reaches the call4 branch. With the start state's a4 (§5) a thread's
first frame has a0 = 0x7FFF_FFFC, whose top bits say call4, so no valid
frame has n = 0; a frame that does has a corrupt a0, and the fill reads that
process's own stack.

**[run]**: `windowed_process`'s alloca test calls `holder`, which holds four
values from a volatile-salted `mix` across a call to `alloca_after_spill`,
which runs a 20-deep call8 chain (spilling `holder` and itself), returns
(reloading itself only), then `alloca`s (clang emits `movsp a1, a8`), fills
the buffer, runs a 10-deep call12 chain (spilling again, now below the new
stack pointer), and checks the buffer; `holder` then checks its four values.
The `alloca` mutant (cause 5 sent to the ordinary trap path) ends the
process with `cause=0x00000005` at the `movsp`, so the exception is taken.
`windowed_alloca` (§10) adds the other two sizes and the first frame, each
held by its own mutant.

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
    l32i a2, F_WINDOWBASE; addi a2, a2, 12  WINDOWBASE is four bits
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
                      main    prototype   build
vector to ktrap        50       112        112
resume to rfe          37       101        100
round trip             87       213        212   +125 instructions per trap
.text.vectors (IRAM)  1303 B    1795 B    1777 B  +474 B of IRAM's 16 KiB
sos_windowed_call       -       126 B      135 B  flash, + 9 B its close
frame                 104 B     304 B      304 B  per thread; stride 512
```

The build's figures are counted on its assembled `boot.S` (`trap_entry` to
`callx0` and `sos_resume_frame` to `rfe` from `llvm-objdump`, plus the
user vector's five) **[run]**. The restore is one shorter than the
prototype's because the four-bit WINDOWBASE takes the sum mod 16 without a
mask; the vectors are smaller because the double path parks nothing and the
Alloca path borrows the caller's dead pane rather than frame words, and the
kernel vector's Alloca dispatch adds back a little.

Every added instruction on the trap path is a `s32i`, `l32i`, `rotw`, `mov`
or SR access in IRAM against the kernel's DRAM, apart from the user
vector's cause test. A window exception costs its
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
F_A4          0x7FFF_FFFC   a windowed entry's return address
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
  1, WINDOWSTART 3, its a0 is the old a4 = 0x7FFF_FFFC and its a2 the old
  a6 = arg0. Pane 0 is a fake caller: a live frame (its WINDOWSTART bit)
  that the thread never returns to (below), and that is spilled first when
  the register file wraps, into the 16 bytes
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

**A windowed entry that returns** executes `retw` with a0 = 0x7FFF_FFFC:
n = 1, a call4 return and an ordinary one, to pane 0 (filled by an
Underflow4 first if it was spilled), at 0x7FFF_FFFC under the top two bits
of the `retw`'s own PC, 01 for flash and IRAM, where nothing is mapped. The
fetch faults, the windowed form of a call0 entry's return to address 0. With
a0 = 0, as revision 1 had it, the `retw` is n = 0, undefined in the ISA: the
emulator raises IllegalInstruction, and a part that did not would land in
the mask ROM's WindowOverflow4 at 0x4000_0000. **[run: build,
`windowed_entry_returns`]**: a child that wraps the register file (so pane
0 is spilled) and returns ends with `SOS: fault instruction-address-error
cause=0x0000000e epc=0x7ffffffc tval=0x7ffffffc`; `lib_a4` (the a4 store
dropped) turns it red. A call0 entry sees a4 = 0x7FFF_FFFC where it saw 0,
which no one-argument entry reads; every call0 case passes.

**An entry point that does not open with `entry`** runs in pane 0 itself, so
pane 0 is a real frame, and its call size is the size of the calls it makes.
A call8 or call12 from it makes its spill an Overflow8 or 12, which reads
its caller's stack pointer at stack_top − 12, a word nothing wrote: the
reviewer's `so112_noentry8` faults storing at 0xFFFFFFE0 at the first wrap.
A `call4` makes its spill an Overflow4, which reads nothing (the reviewer's
`so112_noentry4` returns). So such an entry must make only `call4` calls
from the frame it starts in, and ABI.md §7d says so; `register_file_clean`'s
two entries keep to it. Every compiled function opens with `entry`, so only
hand-written entries are bound, and "no process declares its convention"
holds for an entry that opens with `entry` or keeps to `call4`.

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
0x3C0: rsr  a0, depc            a0 and a2 are scratch: neither path
       j    double_entry        resumes the context the exception took
double_entry:
       l32r a2, =_vector_base
       sub  a0, a0, a2
       movi a2, 0x180
       bgeu a0, a2, 1f          not a window handler
       rsr  a0, ps
       bbci a0, 5, 1f           PS.UM clear: the kernel's
       xsr  a1, excsave1        a1 = the running thread's frame
       j    trap_entry          an ordinary user trap
    1: movi a2, 0x3C0
       j    vec_fatal           kernel_fault: the machine stops
```

Revision 1 parked the window's a0 and a2 in EXCSAVE2 and EXCSAVE3 and put
them back before the trap entry, and question 5 asked to zero the two
afterwards, since a later process can `rsr` them. Neither path resumes the
context the double exception interrupted (the user path ends the process,
the other stops the machine), so the two registers' values are never needed:
the build uses a0 and a2 as scratch outright, parks nothing, and leaves
nothing of the faulting window in any special register, which is what the
ruling on question 5 asks for, by construction. The frame's a0 and a2 words
then hold the double path's scratch rather than the window's; nothing reads
a dead thread's frame. Revision 1's restore lines could be held by no case
for the same reason: their values reached nothing observable.

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

**[run: `windowed_spill_fault4`, `8`, `12` and `windowed_fill_fault4`, `8`,
`12` on the build]**: a windowed C child of a call0 launcher (`death-notify`'s
root) faults through the handler of each size; the teardown line, the
launcher's wake with the status word (`Faulted` << 16, 131072) and its
`done` follow, and the machine ends in `halt pass`. A fill's first load is
a0's (sp − 16, tval 0x10000000) through Underflow4 and a1's (sp − 12, tval
0x10000004) through Underflow8 and 12; a spill's first store is a0's (sp −
16) through each Overflow. The window-handler bound is held at each slot
(`bound100`, `bound0c0`, `bound040`, §10).

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
l32r a7, closed; l32i a7; bnez a7, refused   the loader has begun: refuse
addi a1, a1, -64
s32i a12, a13, a14, a15 -> 4..16         call0's callee-saved; a0..a7 are a
                                         call8 caller's, which the callee keeps
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
l32i a12..a15; addi a1, a1, 64; ret
refused: callx0 sos_windowed_call_refused(fn)     never returns
```

- **Only before the loader** (question 4): a word in the kernel's `.bss`,
  set by `sos_windowed_call_close`, which `lib.saw`'s `payload_start` (the
  loader's first read of the root image) calls. Once it is set the stub
  calls `sos_windowed_call_refused(fn)`, a Saw function in `lib.saw` that
  prints `SOS-S3: kernel bug: windowed call to 0x... after the loader` and
  stops the machine with the core's `KernelBug` status, 6, before `fn` runs.
  The check is in the stub, so no caller can skip it. **[run, build:
  `rom_call_refused`]**: a CRC before `hal.payload_start()` and the refusal
  after it; the `lib_close` mutant (the close call dropped) and every line of
  the check are red.

- **Masking**: INTLEVEL stays 15 throughout, so no interrupt is taken during
  the ROM call; the stub writes PS only to set WOE and back **[run]**.
- **Window state**: a call from a call0 frame needs WOE = 1 and EXCM = 0 for
  `entry`, and a WINDOWSTART with only the caller's bit, because the
  kernel's WINDOWSTART after a trap is the user's (§4.3) and a stale bit
  would make the callee's first reference spill a frame that does not
  exist. **[run]**: `rom_call` sets every WINDOWSTART bit, then calls the
  ROM again, and gets the same CRC. The stub puts the WINDOWSTART it found
  back afterwards; nothing in the kernel reads it while WOE is 0 and every
  resume loads the thread's, so nothing depends on the restore, but it is
  observed: `rom_call` reads WINDOWSTART after the dirty call and asserts
  `0x0000ffff`, and `stub_wsr4` (the restore dropped) is red.
- **Registers**: `callx8` makes the callee's window this frame's a8..a23, so
  the callee clobbers a8..a15; a12..a15 are call0 callee-saved and are
  restored. The 48 registers outside the kernel's window are clobbered, and
  the kernel keeps nothing there; the next resume reloads all 64. **[run,
  build]**: `rom_call`'s `wtest_hold` keeps a value in each of a12..a15
  across a callee that writes its a4..a7 (the stub's a12..a15) and reports
  none lost; each of the four restore lines dropped is red, a12's included.
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

**An Alloca exception under the stub** (a windowed callee's `movsp` with its
caller spilled) arrives at the kernel vector, since PS.UM is 0. Revision 1
stopped the machine there. The build handles it: the kernel vector parks
the faulting window's a0 in EXCSAVE1 (free in the kernel: the trap entry
has stored the user a1 it carried, and every resume writes it again), and
`kernel_alloca` reads WINDOWBASE, rotates one pane back and joins the user
path's `alloca_fill` (§3.6). Making it impossible by construction was not
open: an Alloca needs only a callee that spills its caller and then moves
its stack pointer, and the stub cannot bound how deep a callee nests. The
PLL's ROM functions execute no `movsp` (the reviewer's scan), so they do
not reach it either way. **[run, build: `rom_call`]**: `wtest_alloca12`,
reached through the stub, holds a3..a11 across a call12 to a frame that
spills every older frame, the stub's own included, and then executes
`movsp`; every value comes back. Each line of the kernel vector's dispatch
and of `kernel_alloca` is red when dropped.

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

All in the S3 smoke and run on the emulator. The fixture packages are C and
assembly (`tests/xtensa-windowed*`; eight children share
`tests/xtensa-windowed-child/`'s sources, each package's linker script
choosing the act), plus kernel tests and payloads in `tests/xtensa/`.
**[run, build]**: every case passes, in the gate after each commit.

```
case                    holds                                forced by
windowed_process        start state (ps/wb/ws asserted);     depth 40 > 64/N
                        overflow+underflow at call4/8/12;    registers; a call
                        syscall from depth 30 through the    that spills the
                        windowed stub; movsp under a call8   caller before the
                        caller; two windowed threads         alloca; spins that
                        switched deep in chains; a tick      end only when the
                        between callN and entry (call8,      other thread has
                        call12)                              run
windowed_alloca         the Alloca exception in a first      a wrap of the file
(reviewer's ask 3)      frame and under call4, call8 and     before each movsp;
                        call12 callers holding every         one callee wraps
                        register they own                    again after it, one
                                                             returns at once
register_file_clean     a thread's first instructions find   a thread that filled
                        every register zero but a1, a2, a4   all 64 and exited
                        and a6, each the start state's       from the slot the
                                                             probe reuses; a
                                                             filler that hands
                                                             the probe the CPU
windowed_child_exit     a windowed child exits from depth    death-notify's call0
                        30 beside a call0 Saw root, which    root waits on it
                        wakes with status 65541 and ends
windowed_entry_returns  a windowed entry that returns        a wrap first, so
                        faults on fetch at 0x7ffffffc        pane 0 is spilled
windowed_spill_fault4,  a spill below 0x10000010 through     sp set by hand; the
  8, 12 (ask 4)         each overflow handler ends only      size of the call
                        that process, store-access-fault,    into the frame that
                        tval 0x10000000; its launcher        spills picks the
                        goes on                              handler
windowed_fill_fault4,   the same for a fill through each     a wrap, then sp by
  8, 12 (ask 4)         underflow handler, load-access-      hand and `retw`
                        fault, tval 0x10000000 (call4) or
                        0x10000004
excsave_fault           a double exception with PS.UM = 1    a payload writes
                        outside the window handlers stops    EXCSAVE1 = 0x10000000
                        the machine at 0x3C0                 and traps
rom_call (U3, ask 5)    the ROM's crc32_le; the phy table;   a 24-deep chain;
                        a chain that spills and fills the    wtest_hold's four
                        stub's frame; a12..a15 held; four    values; a callee
                        arguments; PS inside and after; an   that spills the stub
                        Alloca under the stub; the chain     and then movsp; a
                        and the Alloca again from            call0 helper that
                        WINDOWBASE 1; WINDOWSTART all ones   rotates the kernel's
                        before a call and after it           window one pane
rom_spill_fault         a spill fault under the stub stops   sp set by hand
                        the machine at 0x3C0
rom_call_refused (U3)   a call after hal.payload_start()     the loader's first
                        stops the machine, status 6, before  call, made by hand
                        the callee runs
every existing case     call0 unchanged: consoles identical  -
                        to main's but for host time and
                        code addresses (§11a)
```

`windowed_process` has `tick` on, which the thread rounds need. The mixed
case the issue asks for is `windowed_child_exit` and the six fault cases: a
call0 Saw launcher (`death-notify`'s root, which gains an S3 section) and a
windowed C child.

**Mutants [run, build].** Every new or changed instruction line of the
handlers, the user, kernel and double vectors, the double path, the Alloca
paths, the 64-register entry and restore and the stub was replaced by a
`nop` (a label kept), one at a time, and the cases run: red, the source put
back byte for byte (its sha256 checked), green. The value mutants are the
design's thirteen (`save48`/`load48` as every line of `SAVE12` and
`LOAD12`, each a register in all four groups; `ws`, `wb`, `of4`, `of8`,
`of12`, `alloca`, `a6`, `callinc`, `callinc_restore`, `stub12`, `dbl_um`),
the reviewer's (`dbl_100` as `bound100`, `alloca_4`/`alloca_12` as
`af_size4as8`/`af_size12as8`, the underflow swaps as every underflow line,
`stub_a12`..`stub_a15`, `stub_ws`, `stub_ps`, `stub_wsback`), and new ones
(`bound0c0`, `bound040`, `af_size8as4`, `rs_wb0`, `lib_woe0`, `lib_a4`,
`lib_ws`, `lib_words26`, `lib_close`, `lib_refused_exit`). The whole table,
is `build/mutation-table.txt` beside this file (the logs are in
`build/logs-*`); the summary, by the final source:

```
group                              mutants  red  survive
the six window handlers               58     58   0
user, kernel and double vectors,      23     23   0  (dbl_bbci1 is red
  double path, handler bound                           with rom_spill_fault,
                                                       commit 2)
Alloca: user, fill, kernel            30     29   1  af_rsync1
entry: WB/WS save, groups, SAVE12     28     28   0
restore: WS, WB, groups, LOAD12,      34     33   1  rs_rsync1
  callinc_restore
start state and frame (lib.saw)        8      8   0
stub, its close, the refusal          48     45   3  stub_rsync1..3
```

Six more were run on lines the build then removed (below), seven on
`_start`'s reordered lines, and a control (`none`, an unchanged file) that
survives.

The survivors are the `rsync` after a `wsr` (to PS, WINDOWBASE or
WINDOWSTART) before a dependent instruction: the ISA requires the
synchronisation and the emulator does not model the hazard, so no emulator
case can hold one. Mutants that survived at first and changed the build:
`dbl_xsr1` (a double path that put a1 back itself: a trap entry that saved
into the dying window's stack still reported the right fault; the path now
jumps to the user vector, whose `xsr` every trap runs); `dbl_movi1` and
`dbl_bgeu1` (the window-handler bound: held once `excsave_fault` gave a
double exception with PS.UM = 1 outside the handlers); `af_bbsi1`,
`af_bbsi2`, `af_j2` (a fill of the wrong size, masked because the callee's
second wrap re-spilled the stray pane: held once `windowed_alloca`'s second
callee returns straight after its `movsp`); `rs_extui1` (a mask the four-bit
WINDOWBASE makes redundant: dropped); `lib_words26` (`frame_init` zeroing
only 26 words: held once `register_file_clean`'s probe reuses a slot a
pattern-filled thread died in); `ka_rsr1`, `stub_sll1`, `stub_movi2`
(right only because the kernel stood at WINDOWBASE 0: held once `rom_call`
also calls from WINDOWBASE 1); `stub_rsr1`, `stub_s32i6`, `stub_l32i2`,
`stub_addi4` (PS saved, put back and kept at INTLEVEL 15: held once
`rom_call` reads PS inside and after); `stub_mov4` (held once a callee takes
four arguments); `kvec_bnei1` (held by `trap_fault`); `stub_s32i1`,
`stub_l32i4`, `close_movi1`, `close_memw1` (lines that did nothing: the
stub's a0 is a call8 caller's register, which the callee keeps, and the
close needs no constant or barrier; dropped).

The `_start` lines that write PS, WINDOWSTART and WINDOWBASE were reordered
(PS first, through a2) and are not trap-path lines; with the 64-register
restore nothing reads WINDOWSTART or WINDOWBASE before the first resume
loads them, so only `wsr ps` is held (by the timer cases).

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
- EXCSAVE2 and EXCSAVE3 on the part (revision 1's double path used them;
  the build's uses neither; ESP-IDF's S3 port uses EXCSAVE_2..7 **[read]**);
- cycles: the emulator's clock is host time, so every cost here is in
  instructions;
- that the ROM initializes `.data_phyrom` before direct boot on a real
  power-on (it has on the emulator);
- the analog I2C master the PLL path writes.

---

## 11a. What the build ran

The build is two commits on one branch from `74584aca`: `19b48a4d` (U1 and
U2) and `2bb7eda7` (U3). Scripts, logs and transcripts are in `build/`
beside this file.

- The gate, `make sos-test sos-smoke-esp32c3 sos-smoke-esp32s3`, after each
  commit: 979 passed across riscv32, arm64 and riscv32-flat; the C3 smoke's
  6; the S3 smoke's 54 after the first commit and 57 after the second, each
  with the one board-only case not run (`gate1.log`, `gate2.log`).
- The consoles of main's 41 S3 emulator cases, booted from main's images and
  from each commit's (`transcripts/main`, `c1`, `c2`; `compare.py`): 33
  identical; the rest differ only in host-time values (`boot_xtal40`'s and
  `kill_churn_xtal40`'s `mhz=`, `thread_preempt`'s tick lines, letter
  interleaving and `interrupts=`, all printed-only) and code addresses moved
  by the new code (`bus_probe`'s flash-window addresses, the timer cases'
  tick PCs, `trap_fault`'s EPC1).
- The mutants of §10 (`mutate.py`, `spec1.py`, `spec2.py`,
  `mutation-table.txt`).
- The costs of §4.5, from the assembled `boot.S` (`asm.sh`).

The S3's images all change: the trap path, the frame and the start state
are in every one.

## 12. What it costs every thread

- Frame 104 → 304 bytes per thread; `THREAD_FRAME_STRIDE` stays 512, so the
  kernel's frame arena does not grow.
- +125 instructions per trap, switch and first entry, straight-line in
  IRAM; +474 bytes of IRAM, +144 bytes of flash (the stub and its close).
- `kill_churn` should lose very little. At about 24 traps per round, 200
  rounds is about 4,840 traps, so about 610,000 more instructions. At one to
  three cycles each that is 30 to 90 ms at `rom` (20 MHz) against the 26.05 s
  ABI.md §14h measured, 0.1 to 0.35%, and half that time at `xtal40`
  (13.44 s). The tick (`TICK_PERIOD_US` 10,000) adds about 100 traps a
  second of run on the part, another 0.06 to 0.2% at `rom`. This is an
  estimate from instruction counts; the board images of §11 measure it
  against SO-110's numbers.

---

## 12a. The alternative: save the other 48 only at a switch

**Deferred to SO-115.** The owner chose the eager save of §4 for this unit
(§15, question 7); this section is the alternative considered, kept as the
starting point for SO-115, which builds the lazy windowed registers with
lazy FP state on the S3. Nothing below was built here.

The owner asked whether the trap entry should save only the current
window's sixteen registers, and save the other 48 only when the thread
leaves the processor, as arm64 does for FP and SIMD state. This section
evaluates that choice on its own terms, so the owner can pick it instead of
§4's eager save. Nothing in it has run as a kernel: the worktree this unit
had was removed, and creating another was refused by the permission
classifier. So `prototype-lazy.diff` is assembled and counted but has never
been booted. Each claim says which.

### 12a.1 The arm64 model, and the S3's seams

On arm64 the kernel executes no FP instruction, so a trap disturbs no FP
register **[read, `hal/arm64/kernel/lib.saw`]**:

- `fp_trap` marks a thread active at its first FP use and loads its area.
- At every switch, `pick_next` calls `fp_switch_out(outgoing)` while the
  slot is live; it saves the area if the thread is active.
- `run_thread` calls `fp_switch_in(incoming)`; it loads the area and enables
  EL0 access, or disables access.

There is no owner word: after `fp_switch_in` the registers are by
definition the incoming thread's. The S3 has the same two seams as no-ops
in `lib.saw`.

The S3 version of the same model treats the other 48 registers, WINDOWBASE
and WINDOWSTART as switch state:

- **Entry** saves the sixteen and the SRs: today's call0 entry plus the
  cause test. The kernel then runs on the trapping thread's own window with
  PS.WOE = 0 and never rotates, so the 48 stay that thread's while it runs
  **[read: nothing in the call0 kernel, its C or `boot.S` past the entry
  names a window or rotates]**.
- **Switch out** (`fp_switch_out`'s place): an assembly leaf saves the
  outgoing thread's 48 and its WINDOWBASE and WINDOWSTART into its frame.
  It rotates `rotw 3` four times and back by `rotw 4`, so it returns on the
  kernel's own window; it must keep a12..a15, which call0 preserves, and its
  return address, both of which sit in the panes it rotates away from and
  back to.
- **Switch in** (`fp_switch_in`'s place): sets a HAL word, "full load
  pending". It cannot load the 48 itself, because writing WINDOWBASE inside
  a Saw call would rename the kernel's own registers.
- **Resume** (`sos_resume_frame`): if the word is set, clear it and load the
  whole file as §4.4 does (101 instructions). Otherwise restore the sixteen
  as today.

The pending word starts at 1, because the first entry at boot (`process.saw`,
`start_process`) calls `frame_init` and `resume_frame` without
`run_thread`. Those are the only two callers of `resume_frame`; the other is
`run_thread`'s **[read]**.

`prototype-lazy.diff` is the other shape, L1: an owner word (`sos_live_frame`)
that the resume path compares with the frame it resumes, saving the owner's
48 there when the two differ. L1 needs no core seam. L2, the arm64 shape
above, is what I recommend should lazy be chosen, for the reasons in
12a.6. Both shapes have the same entry and the same costs to within a few
instructions.

### 12a.2 Soundness, and every event that touches the state (L2) [read]

The invariant is that every resume of a thread other than the one whose
registers are in the file is preceded by `fp_switch_in`, and every switch
away from a live thread is preceded by `fp_switch_out` while its slot is
live. The core already promises both for FP; this design relies on the same
promise.

```
event                     what happens
trap, same thread back    stays_running: no switch_out or switch_in; short
                          restore. The 48 never left the file.
switch (pick_next)        switch_out saves the outgoing thread's 48;
                          switch_in sets pending; full load
idle, then the same       switch_out saved; switch_in sets pending; full load
thread                    of what was just saved: correct, slower
idle, then another        as a switch
thread exits, current     PENDING_FREE is freed after switch_out: its 48 are
                          saved into a slot about to be freed, then the next
                          thread is fully loaded
process ends under its    resume_after_death -> run_thread -> switch_in:
running thread (exit,     full load. No switch_out: the dead thread's 48 are
fault, kill of own        dropped
process)
killed by another         never the running thread, so its 48 are not in
thread                    the file: the killer's are
slot freed and reused     frame_init zeroes the frame; the new thread's first
                          resume is through run_thread: full load of zeros
first entry at boot       pending starts at 1: full load
double-exception path     the window is rotated, the thread dies: as "process
                          ends" above
Alloca, window handlers   no kernel entry
ROM stub                  refused once any thread has run (12a.4)
```

With no owner word there is no stale owner: nothing ever saves into a frame
except `switch_out` into the outgoing thread's own, live slot.

**A hazard in the core's ordering [read]:** `pick_next` calls
`fp_switch_out(current_frame)` *after* `idle_until_runnable`. During idle,
`drain_deaths` can end the outgoing thread's process, if a delivery dooms it.
The save then writes a slot that may have been freed. That is harmless only
because frames are kernel memory for good (slab donation is permanent,
`slab.saw`) and a freed slot is reused only through `frame_init`, which does
not run in idle. It is the same for lazy FP on arm64 today. The cleaner fix,
for both, is to call `fp_switch_out` before idling; it is a one-line core
change that any lazy unit should take.

**Leaks [read]:** a thread runs only after a short restore of itself, or a
full load of its own frame (zeros at its start). So the 48 another thread
can see are only ever its own. The kernel never rotates, so no kernel value
reaches the 48. The ROM stub runs only before the first entry, which is a
full load. The frame's 48-register area is stale for the running thread;
nothing reads it, and anything that ever does (a register dump) must force a
`switch_out` first.

**Attacks, each against the table above [read]:**

- Killing the thread whose registers are in the file from another thread is
  impossible: the killer is that thread.
- A thread exiting while current: its 48 are saved into its own slot before
  the deferred free.
- A tick inside a window handler: masked, as in §9.
- Two windowed threads ping-ponging: save 48, load 64, every switch.
- A call0 thread and a windowed one alternating: the call0 thread's 48 are
  whatever its frame held (zeros, or what it was switched out with), never
  the other thread's.
- A spill fault, then a switch: the faulting thread's process ends, and the
  switch is through `resume_after_death`, a full load.

### 12a.3 The L1 owner-word shape, for the record [read]

In L1 the resume path keeps `sos_live_frame` and compares it with the frame
it resumes. Every resume names the resumed frame. Exit, kill and teardown
need no update, because a later save into a dead slot writes free kernel
memory. Only `frame_init` must forget a slot it reuses; otherwise the new
thread would inherit the dead thread's 48, WINDOWBASE and WINDOWSTART
through the short path. No path in today's scheduler frees a slot and
initialises it again before the next resume, so no case can turn that guard
red. L1 avoids the idle-ordering hazard, because it saves in the resume
path. It shares nothing with FP.

### 12a.4 The ROM stub

`callx8` rotates the file into whatever thread's 48 are there. Under L2 that
is the last thread run, at any time after the first entry. Two options:

- refuse the call once any thread has run: a HAL word set at the first
  resume, checked by `hal.windowed_call`, a stop if set (the prototype does
  this with L1's owner word);
- save first: a `switch_out` of the current thread, then set pending so the
  next resume reloads it.

Recommend refusing. It is the same rule as question 4 ("ROM only before the
loader"), which the phy table in the ROM's RAM already forces.

### 12a.5 Numbers

Counted on `prototype-lazy.diff`'s `boot.S` (L1), assembled
**[run: assembled, paths counted from the source and objdump]**:

```
path                         today   eager (§4)   lazy L1
trap back to the same thread   87       213        93   (52 + 41)
trap that switches threads     87       213       221   (52 + 169)
first entry, nothing live       -        101      107   (resume only)
.text.vectors (IRAM)        1303 B    1795 B     1879 B
```

L2 is the same apart from the switch's hook calls, about 5 to 10 more
instructions on a switch and 0 to 3 more on a same-thread return (reading
the pending word) **[read, estimate]**.

`kill_churn` under QEMU's exception log **[run]**: 4,839 user-mode traps
over 200 rounds. Consecutive traps change process 402 times, so at least
8% of traps switch threads (switches between threads of one process are
not visible in the log). Weighted, a `kill_churn` trap costs about
0.92 × 93 + 0.08 × 221 ≈ 103 instructions under lazy, against 213 under
eager and 87 today.

A system call from a driver that returns to the same thread, which is the
common case, costs 93 instead of 213: 120 instructions saved, about 6 µs
at the ROM's 20 MHz and 1.5 µs at 80 MHz. In `kill_churn` terms, that
turns the estimated 0.1 to 0.35% loss of §12 into about 0.01 to 0.05%.

### 12a.6 Sharing with lazy FP (K2)

L2 is the arm64 model, on the same two seams, so K2 slots in beside it:

- `fp_trap` claims the S3's coprocessor-disabled cause (32), marks the
  thread active, loads its FP and BR area and sets CPENABLE bit 0.
- `fp_switch_out` saves the 48 always, and the FP area if active.
- `fp_switch_in` sets pending, and loads FP and enables CPENABLE if active
  or clears it if not.

The FP area follows the 64-register area in the same frame; 304 + 76 =
380 bytes, inside the 512-byte stride **[read]**. The two halves share the
seams, the frame, the core's switch-out ordering fix, and the leak case
below. They do not share a word: the windows have no "active" bit, because
every thread has 48 registers.

Building the windowed half now makes the FP half easier. It turns the S3's
seams from no-ops into real code and proves them under the windowed cases.
K2 then adds an "if active" branch to each hook and the cause-32 claim.
Building eager now and lazy later means writing the S3's switch path twice;
the eager save code is reusable as the `switch_out` leaf, so the second time
costs little.

### 12a.7 Cases and mutants for lazy

On top of §10's cases, which all stay:

- **`register_file_clean`** (worth having under either choice): a fresh
  windowed thread rotates through all 16 panes and checks every register is
  the zero `frame_init` promises; it runs after another process has filled
  its own file with a pattern.
- **`lazy_switch_deaths`**: one thread exits while current, then another
  runs; a process ends under its running thread, then another runs; each
  survivor checks its registers through a deep chain.
- `windowed_process`'s thread rounds (ping-pong), `frame_hold` beside a
  windowed thread (call0 and windowed alternating), and
  `windowed_spill_fault` (a spill fault, then a switch) as they are.

Mutants, to be run once the prototype boots:

```
mutant (L2 / L1)                         expected to turn red
switch_in does not set pending /         thread rounds; register_file_clean
  owner not updated at a resume
switch_out does not save the 48 /        thread rounds
  outgoing 48 not saved
full load skipped (short path always)    thread rounds; register_file_clean
pending starts at 0 (L2)                 register_file_clean only: the
                                         first thread's start state is
                                         otherwise what the kernel left
frame_init does not forget (L1)          none: unreachable today (12a.3)
switch_out after a death in idle         not forceable on the emulator
  (the ordering hazard)                  without a scratch kernel; read
```

### 12a.8 Recommendation, and the units under each choice

Lazy (L2) is sound by reading, but none of it has run. It wins about 120
instructions on every system call that returns to its caller, and loses
about 8 on a switch. Its correctness rests on the same switch-seam promise
lazy FP rests on, plus one core ordering fix.

**Recommendation: build eager (§4) now, and build lazy as one unit with
K2's FP state, in the L2 shape.**

- Eager is the only one of the two that has run: 46 smoke cases and 13
  mutants.
- Its cost is under half a percent of `kill_churn`.
- The radio driver's system-call rate is not yet known.
- Lazy and K2 share every seam, so they are cheapest and best tested
  together.

I would choose lazy now instead if the owner wants the per-trap cost gone
before K2, or if a measured driver turns out to make system calls fast
enough that 6 µs each matters at the ROM clock.

**Units if eager now** (as §14):

- U1, U2 and U3 as listed;
- U2 also gains `register_file_clean`;
- U4 board and U5 windowed Saw unchanged;
- **U6 (M), later with K2**: L2 lazy windows and lazy FP, the core's
  switch-out ordering fix, `lazy_switch_deaths` and the lazy mutants. The
  eager save becomes the `switch_out` leaf.

**Units if lazy now**:

- **U1' (M)**: entry without the 48; `switch_out` leaf, `switch_in`
  pending word, the resume's two paths; the core's switch-out ordering fix
  (`kernel/core/sched.saw`); the ROM-refused-after-first-entry rule;
  ABI.md and the spec sentence. Gated by U2' on the emulator.
- **U2' (S)**: §10's cases plus `register_file_clean` and
  `lazy_switch_deaths`, and the lazy mutants.
- U3, U4 and U5 as before.
- K2 adds FP to the same hooks.

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
thread. Revision 1 had two special registers do: its double path parked
the faulting window's a0 and a2 in EXCSAVE2 and EXCSAVE3, which any later
process can `rsr`. The build parks nothing there (§6.1, question 5), so no
special register carries a faulting window's values. EXCSAVE1 holds the
running frame's address while user code runs, as today. **[run, build:
`register_file_clean`]**: a thread's first instructions read all 64
physical registers after another thread filled every one with a pattern and
handed the processor over by blocking; only a1, a2, a4 and a6 are nonzero,
each the start state's. With one register of the restore's `LOAD12` dropped,
the pattern shows through (the `ld_*` mutants, §10).

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

**As built (SO-112):** U1 and U2 are the first commit, U3 the second, on
one branch. U2 has the reviewer's cases (Alloca in a first frame and under
call4 and call12, a spill and a fill fault at every size) and
`register_file_clean`; U1's double path parks nothing in EXCSAVE2/3 rather
than zeroing them (§6.1). The mutants are recorded in §10, not in the cases'
comments. U4 and U5 are not started; the lazy save is SO-115.

---

## 15. Questions for the owner, answered

The owner ruled on Nov 15, every question as recommended but 6, which took
the reviewer's alternative, and 7, which chose eager now.

1. **Save all 64 registers on every trap, or spill live windows to the
   user's stack?** *Answered: all 64, on every trap; never spill to a user's
   stack* (§4.1).
2. **One start state for both conventions (CALLINC 1, arg0 in a2 and a6),
   with no declaration in the image?** *Answered: yes* (§5). An entry that
   does not open with `entry` keeps to `call4` from its first frame
   (ABI.md §7d).
3. **Report a fault inside a spill or fill with the access's own cause and
   the user instruction as the PC, or as a distinct reason?** *Answered: the
   access's own cause* (§6.2).
4. **The ROM's RAM.** *Answered: the kernel calls the ROM only before the
   loader, checked at the stub, and the pool stays where it is* (§7.1): the
   loader's first read of the root image (`hal.payload_start`) closes
   `sos_windowed_call`, which refuses a call after that and stops the
   machine. The reviewer's option of writing the analog I2C master directly
   and calling no ROM function for the PLL is SO-113's to weigh.
5. **Zero EXCSAVE2/3 after the double path?** *Answered: yes.* Built by
   construction instead: the double path parks nothing in them, since
   neither of its paths resumes the interrupted context (§6.1), so nothing
   of a faulting window is ever in either.
6. **What a windowed entry that returns should be.** *Answered: the
   reviewer's method, not an untrapped `retw`*: the start state's a4 =
   0x7FFF_FFFC, a call4 return address that faults on fetch (§5), with a
   case (`windowed_entry_returns`).
7. **Save the other 48 only when the thread leaves the processor, as arm64
   saves FP state?** *Answered: eager for now.* The lazy variant (§12a) is
   deferred to SO-115, to be built with lazy FP state on the S3.

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

**As built:** the §5b paragraph changed as above, in the first commit; §5.7
is unchanged.
