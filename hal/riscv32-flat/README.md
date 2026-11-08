# `hal/riscv32-flat` — the flat build profile of the riscv32 `virt` board

SOS's tier-3 demonstration: the flat end of the memory-protection tiers. It is
one of the profiles `make sos-test` runs.

This is not an architecture and not a board. It is the same riscv32 QEMU `virt`
machine the `riscv32` profile runs, built with one module swapped: a protection
seam whose bodies are empty, whose PMP is opened once to permit everything, and
whose tier word says `Flat`.

## What is here, and what is not

```
kernel/lib.saw      the `hal` module for this profile — the protection
                    seam, the tier word, and the boot banner. That is all.
README.md           this file.
```

There is deliberately no `boot.S`, no linker script, no `sink.c`, no `trap.S`,
no `user/` directory and no `ABI.md`. Every one of those is the board's or the
architecture's, and a protection profile changes neither. The runner reaches
them where they live:

| what | where it comes from | why |
|---|---|---|
| `boot.S`, `virt.ld` | `hal/riscv32/kernel/` (the `hal_board` key) | the stack, the payload section, the region table and the memory map are the board's |
| `trap.S`, `sink.c` | `hal/riscv32-common/kernel/` | the trap entry and the CSR leaves are the arch's |
| `user/syscall.c` | `hal/riscv32-common/user/` | the `ecall` stub is the arch's |
| `user/root.ld`, `child*.ld` | `hal/riscv32/user/` | the link bases are the board's, and identity addresses are what both riscv32 profiles use |
| `tests/riscv32/*.S` | shared (the `tests_arch` key) | the hand-assembled payloads are riscv32 instructions |
| root and child `.sosimg`s | shared (same target triple) | userspace is byte-identical; nothing about a process image knows the tier |

The only thing this profile does not share with `hal/riscv32/` is the `hal`
module and its own `.build/` directory (the `build_tag` key — two profiles on
one triple must not relink each other's objects).

## The three modules

riscv32 is split into an arch half and a board half. This profile is a third
level of the same idea, built by naming the other two rather than by copying
either:

```
rv32core   hal/riscv32-common/kernel   the arch: trap frame, PMP, cause decode
rv32virt   hal/riscv32/kernel          the board: UART, CLINT, PLIC, memory map
hal        hal/riscv32-flat/kernel     this: the protection profile
```

`rv32virt` is the virt board's own `lib.saw`, under a second module name — the
identical directory, which the runner maps as `rv32virt=hal/riscv32/kernel` for
this profile where it maps it as `hal=hal/riscv32/kernel` for the isolated one.
A Saw module's name comes from its `--module-path` mapping, so one directory can
be the board's `hal` in one build and a component of this profile's `hal` in
another.

That keeps `riscv32-common` the one arch home and `hal/riscv32/` the one virt
board: adding this profile edits neither.

The arch names are taken from `rv32core` directly rather than forwarded through
`rv32virt`, which also re-exports them. The names this profile must not inherit
are exactly the protection ones, so taking the arch half by name makes the
omission visible in one list instead of hidden in a chain.

## What "flat" means, precisely

`prot_isolated()` answers `false`, so `SystemOp.TierGet` answers
`ProtectionTier.Flat`. The tier carries these consequences:

- **`map` succeeds honestly.** Its contract is "make this region accessible with
  at least these rights"; here every region already is, so the contract holds
  and the call is not a lie. The kernel still records the row, still charges the
  quota, still hands back a `Mapping` that still owns it.
- **`unmap`'s revocation half is disclaimed.** The row leaves the record and the
  Mapping dies; the hardware goes on permitting the access.
- **Peer isolation is disclaimed.** A process can reach another's memory.
- **Protecting memory from a process is disclaimed, the kernel's own included.**
  The user-mode PMP range is read/write/execute over the whole address space, so
  a process can read and write any address, including kernel memory and the test
  finisher. Handles, rights, quotas and lifetimes are checked identically to the
  isolated profile on every syscall, but a process that writes kernel memory
  directly bypasses all of them (`SystemRight.Shutdown` included). The
  object-model suite runs here unchanged and passes because every case reaches
  the kernel through syscalls; `tier_word`'s third assertion is a rights refusal
  on that path.

## PMP is opened, not left alone

The one thing worth knowing before editing this file.

"Leave PMP open" cannot mean "write nothing". RISC-V gives default-deny for
free: if no PMP entry matches a U-mode access and at least one entry is
implemented, the access fails. At reset every `pmpcfg` byte is zero (A=OFF), so
a profile that merely emptied the `prot_*` bodies would deny root its own first
instruction.

This profile therefore takes one affirmative action — a single TOR entry
spanning `[0, 4 GiB)` with R|W|X — and that is the only hardware it touches. It
is published from both `prot_commit` and `prot_switch`, because those are the
two seam calls that survive `prot_replay_at_switch()` answering `false` on the
paths that matter:

- `load_domain` calls `prot_switch` unconditionally (it is outside the replay
  guard), so that is the real kernel's path and it is guaranteed to run before
  root enters U-mode;
- the harness kernels under `tests/` that program the seam directly
  (`umode.saw`, `timer.saw`, `timer_mask.saw`, …) end at `prot_commit`, so
  `prot_switch` alone would leave them with a dead PMP.

Both are stateless and idempotent. Doing the work every time rather than once
behind a flag keeps the profile free of a "have we opened it yet" word to get
wrong on a path nobody tested.

## Which cases do not run here

These cases are excluded by name with a reason in the runner's report — no
silent skips. Each one's assertion is that the hardware denied something, which
is the one thing this platform cannot do:

| case | why |
|---|---|
| `process_isolation` | peer isolation is what `Flat` disclaims by name |
| `map_unmap` | `unmap`'s revocation half, the other disclaimed property |
| `umode_access_fault` | a U-mode store into kernel `.text` must fault |
| `root_server_oversteps` | root reaching into kernel memory must fault |
| `stats_region_ro` | a store into the kernel's read-only counter page must fault |

Everything else runs, including every case whose refusal comes from the kernel
rather than the hardware — `map_wx_refused` and `map_exec_gated` among them,
whose W^X and execute-right checks are argument checks in `kernel/core` on the
syscall path, and hold on every tier.

## Adding a profile

A new board follows `hal/riscv32-common/README.md`. A new profile of an existing
board is this directory's shape: a `lib.saw` that re-exports the arch half from
`rv32core` and the board half from the board's module, defines whatever it means
to change, and a runner entry carrying `hal_board`, `tests_arch`, `build_tag`,
`banner_arch` and `tier`. Nothing in `riscv32-common` or in the board moves.
