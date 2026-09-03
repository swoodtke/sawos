# SawOS design 26 — the sosabi module split (housekeeping)

**Status: DISPATCHED Sep 3 2026** (user-requested, same day: "break up
the kernel's abi lib.saw into smaller files — it is currently over
3.7k lines"). Mechanical, no behavior change, no ABI change — the
gate is BYTE-IDENTICAL transcripts on both architectures.

## The shape

`kernel/abi/src/lib.saw` (3,735 lines) is the `sosabi` module: every
op number, rights bit, status tag, handle type and wire record in the
system, deliberately ONE PLACE (its own header says why). The split
keeps the one-place DOCTRINE — one PACKAGE, one importable name — and
retires the one-FILE reading: `lib.saw` becomes the `sosabi` facade
(`public import` of leaf modules, the design-23 pattern), and every
existing `import sosabi.{...}` site in `kernel/core/` and
`kernel/sysapi/` keeps working UNCHANGED. Zero importer churn is a
requirement, not a preference.

## Proposed file map (the section banners are the cut lines)

| file | contents (today's lines) |
|---|---|
| `types.saw` | Object types (62–213) + Handles §3 (214–420) |
| `rights.saw` | the 15 `*Right` enums (421–1258) + WHAT EVERY DEFAULT SET MINTS (1259–1588) — the default sets read the rights, same file |
| `ops.saw` | the universal ops (1589–1694) + every per-object op table (1695–2698) |
| `records.saw` | the wait record (2699–2975), Clock/Timer records (2976–3038), pipe records (3182–3354), process-stats record (3355–3423) |
| `boot.saw` | the boot-handle record (3039–3181) + the boot handle set (3724–end) |
| `status.saw` | status words (3424–3617), fault reasons (3618–3693), process status (3694–3723) |
| `lib.saw` | the module doc (the vDSO-discipline header, kept verbatim) + the facade `public import`s |

The map is a PROPOSAL: the implementing agent verifies intra-file
references and may re-cut where a cycle forces it (leaf modules must
be acyclic; when two sections reference each other they share a
file, stated in a one-line comment at the seam). Section prose and
doc comments MOVE VERBATIM with their declarations — this unit
rewrites nothing.

## Constraints and cautions

- Load the saw-lang skill first. Facade re-export is proven ground:
  extension lookup follows `public import` since sawlang 0.2.0
  (SL-7's fix), and design 23's keeper proved re-exported statics
  still FOLD two hops away. Enum methods (`from(raw:)` etc.) live in
  their type's declaring module and move with it.
- Module naming: `sosabi.types`, `sosabi.rights`, etc. — nothing
  outside the package may import a leaf directly (the facade is the
  one door; leaf declarations `public`, but the kernel-internal
  discipline is unchanged and the header still says so).
- No renumbering, no reordering of enum members, no renamed
  declarations, no new declarations.
- Gate: `make sos-test` (suite lock protocol), transcript diffed
  against a pre-split baseline — byte-identical, both arches, or the
  unit reports why not and stops.

## As built

**Status: BUILT Sep 3 2026.** Six leaves and a facade; the suite transcript is
BYTE-IDENTICAL to the pre-split baseline, both arches, 232/232.

### The file map (as built — the proposal held)

| file | lines | contents (source lines it holds) |
|---|---|---|
| `lib.saw` | 184 | the module doc, lines 1–60 VERBATIM, plus a facade section — and 138 named `public import`s. No declaration. |
| `types.saw` | 368 | Object types + Handles §3 (62–420) |
| `rights.saw` | 1177 | the 15 `*Right` enums + WHAT EVERY DEFAULT SET MINTS (421–1588) |
| `ops.saw` | 1125 | the universal ops + every per-object op table + the trailing argument encodings (1589–2698) |
| `records.saw` | 598 | the wait record + Clock/Timer records (2699–3038), pipe records + process-stats record (3182–3423) |
| `boot.saw` | 163 | the boot-handle record (3039–3181) + the boot handle set (3724–3735) |
| `status.saw` | 309 | status words, fault reasons, process status (3424–3723) |

**THE PROPOSED CUT DID NOT MOVE.** The brief's ranges were taken as-is. The
dependency graph over them is a near-trivial DAG with exactly ONE edge:
`records` imports `PIPE_BODY_BYTES`, `PIPE_MSG_HANDLES` and `PipeMsgMeta` from
`ops`, because `WaitBuffer`, `PipeMsgRecord` and `PipeTakeRecord` are sized in
them. It is one-way — nothing in `ops` names a record — so no two sections had
to share a file and no cycle had to be broken. `records.saw` carries the
one-line note at the import and the facade header repeats it.

Worth recording because it is the one place the map reads oddly: those three
names are RECORD vocabulary sitting in `ops.saw`. They are there because the
brief's rule was "the section banners are the cut lines" and no banner separates
them from the pipe op tables — the same stretch also holds `ClockType`,
`MapAccess`, `MAP_ACCESS_MASK` and `EventMode`, which are argument encodings
rather than op numbers. A future unit that wants `ops.saw` to be op numbers
alone has a clean seam at line 2473 of the pre-split file and one import to
retarget; it was not taken here because nothing forced it and this unit's
contract was to move nothing that did not have to move.

**THE MOVE IS MECHANICALLY VERIFIED VERBATIM.** The split was done by line-range
extraction, never by retyping, and a separate checker strips each leaf's new
header, reassembles the bodies in the original file's line order and demands an
exact match: all 3,674 declaration/prose lines placed exactly once, no line
missing, none duplicated, and `lib.saw`'s doc lines 1–60 byte-identical. No
rename, no renumber, no reorder, no rewritten prose.

### The finding that changed the facade — SL-20

The facade was first written as six globs (`public import sosabi.types.*`), on
the argument that a kernel-internal one-place module has no surface to curate.
That does not compile, and the reason is a language deficiency now filed as
**SL-20**: **a glob import does not bind a `type` alias.** `public import
sosabi.types.*` re-exported the statics, the enums and the functions and
silently dropped all fifteen handle aliases, so `MemoryHandle` became a
name-only type at the importer — it no longer flowed to `UInt` and its
back-construction was gone — and the errors landed in
`kernel/sysapi/src/memory.saw`, a file this unit never touched:

```
error: argument `handle` expects `UInt` but got `MemoryHandle`
error: undefined function `MemoryHandle`
```

Isolated with a three-file probe rather than guessed at: the plain glob fails
identically to the `public` one (so it is not SL-7's re-export question), and
the diagnostic's own hint enumerates the survivors — ``available: SIZE, Tag,
consume`` — which is what named the missing declaration kind. The selective form
carries an alias WHOLE, probe-verified in every position the split needed:
construction, annotation, struct field, parameter, return, and the implicit
widening to the underlying, two hops from the declaring module.

So the facade names all 138 symbols, GENERATED from the leaves' public
declarations rather than curated — 138 is exactly the count of
`^public (enum|struct|type|func|static)` in the pre-split file, which is what
makes zero importer churn a proof rather than a hope. It also lands the module
on the same discipline `kcore`'s facade and `sos`'s already use.

### Gate evidence

Same suite lock, same runner, same tree otherwise; both runs foreground.

| | cases | result | sha256 of the transcript |
|---|---|---|---|
| baseline (merge-base, `56ad00e`) | 232 = 116 × 2 arches | ALL PASSED | `3f6dde15f0f9e3f3cea88bcd3976902414028054c323c96afea74b0460add307` |
| after the split | 232 = 116 × 2 arches | ALL PASSED | `3f6dde15f0f9e3f3cea88bcd3976902414028054c323c96afea74b0460add307` |

`diff` is empty — **BYTE-IDENTICAL**, all 483 lines, riscv32 and arm64. The
baseline's hash also equals the one design 24 recorded, so the transcript is now
reproducible across four runs on two trees, and the three known
timing-dependent rows did not move either.

The gate ran TWICE after the split: once on the tree the split produced, and
again on the COMMITTED tree after the facade header was rewrapped and its
sawc-version reference corrected. A doc comment cannot reach codegen, but the
rule here is that the tree which gates is the tree which commits, and the second
run hashes the same.

The transcript carries every image's SIZE IN BYTES, so byte-identical is the
stronger claim it looks like: not one `.sosimg` in the tree changed by a byte.
That is the split's real acceptance — a module reorganization that reaches
codegen is not a module reorganization.

**Zero importer churn, verified by `git status`:** the only tracked file
modified is `kernel/abi/src/lib.saw`; the six leaves are new. Nothing under
`kernel/core/`, `kernel/sysapi/`, `tests/`, `root/` or `hal/` was edited. 28
files outside `kernel/abi/` import this module, and all THREE spellings they use
had to keep working, which they do: the 27 selective `import sosabi.{...}`
statements; the six `public import sosabi.{...}` re-exports in
`kernel/sysapi/src/lib.saw`, which now forward names through two facades and
still land on the vDSO wall exactly where they did; and
`kernel/sysapi/src/system.saw`'s whole-module `import sosabi`, which reaches
`sosabi.ENTRY_IMAGE` through the QUALIFIER — the spelling most at risk from a
facade, since it asks the module's surface directly, and the one that made the
138-name list worth generating rather than curating.

No manifest change was needed: `--module-path sosabi=<dir>` already names the
DIRECTORY, exactly as `kcore` and `sos` are named, so sibling discovery came
free.
