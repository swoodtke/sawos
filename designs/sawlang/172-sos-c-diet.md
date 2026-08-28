# Design 172 — the SOS C diet: rewrite into Saw what is expressible today

**Status: DONE, in two parts. Part 1 landed units 1, 3, 4, 6, 7 and 8 and
STOPPED unit 2 on DF-172e; part 2 (Aug 7, after design 177 closed that finding)
landed unit 2 and the process side of the same seam family. Unit 5 filed
DF-172a, which is the "legit unexpressible" case the user named. Approved by
the user, Aug 7: "let's rewrite into saw what is expressible today. if there
are legit unexpressible items (that don't require asm) we should note it for
future work" — the foundation pass BEFORE M2's interrupt/scheduling work. SOS
policy: the branch PARKS for user review.**

## Why

M1b landed 803 lines of C across the two HALs + common runtime. Post-audit,
the C sorts into three buckets; only the first is legitimate. The end state
this brief buys: **every remaining C line carries a one-line stated reason it
is C** — an auditable unsafe floor before the OS work gets hairy (M2).

1. **Legitimately C (stays, each with its reason comment):** inline-asm-bound
   bodies — semihosting exit (`hlt` + pinned registers), `wfi` loops, riscv32
   CSR writes (assembly-time immediates), the arm64 MSR/TLBI/barrier MMU
   activation sequence, both user-side syscall stubs (`ecall`/`svc`) — plus
   `memcpy`/`memset` (LLVM loop-idiom recognition rewrites a Saw byte loop
   INTO memcpy — self-recursion, permanent, already in the tracker).
2. **Saw-able today (this brief moves it):** see units.
3. **Expressible-looking but gapped:** anything that turns out to need a
   language feature (not asm) gets a DF-172x finding and STAYS C for now —
   noted for future work, per the user's rule. The known candidate is the
   linker-symbol accessors (below).

## Units

1. **arm64 page-table construction → Saw.** The static identity map's
   descriptor building is u64 stores through the design-112 `UnsafeMemory`
   machinery; only the activation sequence (MSR/TLBI/barriers) stays as a
   C/asm leaf taking the finished table's address. The table builder joins
   `sos/hal/arm64/kernel/lib.saw`. The grant-window alignment check comes
   with it (it is logic, not hardware).
2. **The arena allocator → Saw.** `support.c`'s early arena is compound
   global state — design 149's `unsafe static var` + SpinLock machinery was
   built for exactly this. One arch-free implementation in Saw serving both
   kernel and processes (the DF-140g direction, first concrete step).
3. **Kernel-bug printing → Saw.** `put_str`/hex dumping in the sinks
   duplicates `sos/rt/common/` helpers that already exist in Saw — the ktrap
   report path composes them instead.
4. **UART write loops → Saw, with the panic-recursion pin.** Both consoles
   already have Saw drivers (design 112) for the normal path; the C copies
   exist only for the panic seam. The Saw replacement MUST be check-free by
   construction — raw pointer stores, wrapping arithmetic (`&+`), no
   allocation, no indexing — so a panic inside the reporter cannot re-enter
   it. Verification is a test: a deliberately-panicking kernel entry whose
   panic output must arrive intact on both arches (panic-in-panic cannot
   hang or garble). If check-freedom cannot be GUARANTEED by inspection of
   the emitted IR (`--emit-ir` on the writer), this unit STOPS and files the
   finding — the panic path does not get best-effort.
5. **Linker-symbol accessors — probe, then move or file.** The C functions
   returning `__kernel_end`-class addresses exist because Saw has no way to
   name an externally-defined symbol's ADDRESS. Probe whether `extern` +
   the DF-163f-blessed `(&sym) as UnsafePointer<T>` shape can express it
   today; if not, file **DF-172a: extern static declarations** (the address
   of a linker symbol as a first-class value) as the future-work note and
   keep these C bodies — they are the "legit unexpressible without a
   language feature" case the user named.
6. **The reason-comment sweep + docs.** Every surviving C line's file gets
   its bucket-1 reason stated at the top; `sos/rt/common_c/support.c`'s
   header comment and the two HAL ABI.md files updated; sos/spec.md notes
   the C floor; tracker records the before/after line counts.
7. **[ADDED, user Aug 7] DF-162a: freestanding aarch64 defaults to
   `-neon,-fp-armv8`** unless `--target-features` overrides — COMPILER-side
   unit, its commits isolated for immediate cherry-pick to main per SOS
   flow. The arm64 HAL build then drops its explicit flags; the
   panic-in-panic test (unit 4) exercises the default on real kernel code.
   Spec/skill note the default and the override.
8. **[ADDED, user Aug 7] sosimg v3: 64-bit addresses.** Header 16→24,
   record 20→24, fixtures regenerated on both arches, Blade emitter +
   kernel loader + imgformat typed views all move together; the v2 refusal
   path becomes the v3 wrong-version refusal (a v2 image is a clean load
   error, not a guess). Wire fields stay explicit fixed-width little-endian.

## Gates

Per-unit commits, full battery each (suite zero xfails, lexdiff, astdiff,
Saw-irdet --all, bootstrap, gmgate, sos_runner BOTH ARCHES — either arch
failing is red). The panic-in-panic test from unit 4 joins the sos test set
permanently. DF-172x findings for gaps, fixed or filed, never worked around.
Branch PARKS for user review (SOS policy).

## Part 2 — the landing report (Aug 7)

Unit 2 was the one thing part 1 stopped on, and it resumed exactly as written:
DF-172e was the only blocker, design 177 closed it, and nothing else about the
move had changed. It also turned out to cover more than the arena. The seam
FAMILY has two ends, and the process end was C for the same reason — both user
`syscall.c` headers said so in their own words: *"the two hooks + the parked
handle : these ARE expressible ... When that lands, this file should be
`sos_syscall1` and nothing else."* So part 2 landed both ends.

**What is Saw now.** The four `__saw_rt_*` seams and the bump arena, in `sosrt`
(one copy, kernel and every process). The process side's `sos_rt_write`,
`sos_rt_abort` and its parked boot handle, in `sos/kernel/sysapi/` beside the
System object whose authority they spend — arch-free, so two per-arch C copies
became one Saw one.

**The C, measured** (code lines: non-blank, non-comment):

| file | M1b | after part 1 | after part 2 |
|---|---|---|---|
| `sos/hal/arm64/kernel/sink.c` | 170 | 47 | 47 |
| `sos/hal/riscv32/kernel/sink.c` | 75 | 22 | 22 |
| `sos/hal/arm64/user/syscall.c` | 32 | 32 | **11** |
| `sos/hal/riscv32/user/syscall.c` | 31 | 31 | **11** |
| `sos/rt/common_c/support.c` | 75 | 75 | **44** |
| **total** | **383** | **207** (-46%) | **135** (-65%) |

Every surviving line is bucket 1 (an instruction) or bucket 2 (`mem*` + the
atomic libcalls). Bucket 3 has exactly one member left, DF-172a, and it is the
one the brief predicted.

**Three deliberate improvements over the C**, none of them translation:

1. The arena aligns the ABSOLUTE ADDRESS it hands back, not the offset. The C
   rounded `arena_next` and returned `&arena[p]`, which only satisfies the
   caller if the REGION is itself as aligned as anything asked of it.
2. `sos_rt_abort` is DECLARED `-> Never` on both sides. What was
   `__attribute__((noreturn))` — a promise the type system could not see — is
   now a type, and design 177 is what makes a Saw body able to keep it.
3. The region's size has ONE spelling, in a named array type, with `sizeof`
   reading it back and a length mismatch a compile error.

**Four findings, all filed, three of them fixed here.** DF-172f and DF-172g are
compiler ICEs the single-source-of-truth spelling walked into; DF-172h is a
`-> Never` extern lowering to an i8 placeholder instead of `void`, which the SOS
seam shape is the first thing in the tree to hit. All three are isolated
commits for cherry-pick to main. DF-172i is a coverage note, not a bug: the
kernel's exported typed C surface has lost its only in-tree CALLER.

## Explicitly out

Inline asm or per-instruction intrinsics in Saw (a separate design
conversation if ever); touching `memcpy`/`memset`; the syscall stubs and
exit/CSR/MSR leaves; test payloads (`sos/tests/*/payload_*.S`,
`fault_target.c` — fixtures simulating foreign binaries, non-Saw on
purpose); any M2 feature.
