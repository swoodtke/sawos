# SawOS design 31 — M7 SEED: the POSIX compatibility layer (toybox first)

Status: SEED, Sep 3 2026 — user-ruled during the M5 run (the
design-19 pattern). Not a unit brief; nothing dispatches from this.

## The ruling (user, Sep 3)

M7 is a POSIX COMPATIBILITY LAYER whose first target is TOYBOX, then
other utilities. **The feasibility hinge, named by the user: toybox
supports `posix_spawn`-shaped process creation, not just
fork/exec** — and spawn is the process-creation shape a capability
system can honestly provide. This moved the verdict from
"personality-layer moonshot" (the Sep-3 assessment before the
ruling) to "a libc shim plus a spawn protocol".

## Why spawn changes everything

`fork()` without `exec` is the impossible call on a capability
system: it duplicates ambient state (an address space AND a handle
table) that SOS deliberately makes explicit. `posix_spawn` is the
OTHER shape — name a binary, pass argv/envp/fd-dispositions, get a
child — and it maps almost 1:1 onto machinery that EXISTS:

| POSIX | SOS |
|---|---|
| `posix_spawn(path, ...)` | loader resolves path (M6 namespace) → `ProcessCreate` (image as a Memory handle — already the ABI) → `give` the boot set → `start` |
| file_actions / fd inheritance | the boot handle set: tags are the fd numbers, handles are pipes/fs capabilities |
| `waitpid` | a Waiter on the child's Process handle — waitable since M3 unit 5.5, terminal-level, no lost-edge race |
| exit status | the §8 status word `GetStatus`/wait already answers |

Toybox's own nommu-Linux discipline is the enabler: nommu targets
cannot fork-without-exec either, so toybox is already structured
around vfork+exec / spawn shapes (`XVFORK`, NOFORK toys). We ride
that discipline rather than fighting the codebase.

## The layer's parts (sized honestly)

1. **A C-facing sos surface** — and the vDSO discipline already
   planned for it: the sysapi's naming rule mirrors every typed
   method as a C export (`sos_system_clock_get`, ...), and the HAL
   syscall stub is already C. The libc shim sits on those exports;
   numbers stay non-ABI.
2. **The libc shim** (sos-libc): malloc over the sosrt arena /
   Memory splits; stdio over pipes to the console service; open/
   read/write/close over the M6 fs+namespace protocol (fd = index
   into a userspace table of handles); spawn/waitpid as above;
   errno mapped from `SosStatus`; signals MINIMAL (toybox needs
   little; delivery via the wake bridge where wanted). A musl port
   is the fallback if the shim's surface creeps toward completeness.
3. **The spawn protocol convention**: argv/envp ride a Memory
   region the parent fills and gives under a well-known tag; fd
   dispositions are boot-set tags. This is a USERSPACE convention —
   no kernel change, which is the design test.
4. **Toolchain**: clang already targets both arches freestanding
   (the esp-clang note in design 19's addenda); crt0 + linker
   scripts per the sosimg discipline. Toybox builds as ONE binary
   dispatching on argv[0] — one sosimg, many names in the archive.

## What stays hard (recorded so the scoping session prices it)

- **toysh job control** — process groups, tty semantics, `Ctrl-C`:
  the deepest POSIX assumptions in toybox; likely last, possibly
  descoped in favor of a native shell that spawns toybox utilities.
- **`mmap`** beyond file-backed-read (shim over Memory/map; real
  file mapping wants M5 placement + M6 fs cooperation).
- **fork if anything demands it**: answered NO — a utility that
  needs bare fork is out of scope; M5's per-process address spaces
  make a copying fork *technically* expressible one day, but it is
  not this milestone and not this kernel's doctrine.

## Order of attack (proposed for the scoping session)

Single utilities with no tty needs first (`echo`, `cat`, `ls`,
`sha1sum` — file-reading proves the whole M6 stack), then the
multiplexed binary + spawn from a native launcher, then toysh or a
native shell, decided when the tty story is priced.

## Adjacent

Anchors on M6 (`designs/030`) for the loader, fs, and namespace;
consumes M5's allocator/donation/placement throughout. The
lazy-handle-decode backlog entry's revisit trigger (a genuinely
tight-SRAM target) is unaffected — this milestone targets the QEMU
virt boards, not MCU-class parts.
