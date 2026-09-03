# SawOS design 24 — the M4 docs sweep (unit 6, closes M4)

The ladder's last rung (#10: "§2.1 flips to BUILT, §2.2 waitable
list closes, §11 refresh, the M4 recap"), run on design 11's M3
model: an owed list with sources, a consistency grep, no code. When
this integrates, M4 CLOSES and the M5 scoping session unlocks
(designs/019 the anchor). Review WAIVED (user, Sep 2 night — "i will
take a look at the final result tomorrow morning"): this brief is
the record; the user reviews the landed sweep, not the plan.

## The owed list (each with its source)

1. **spec.md §2.1 flips to BUILT** — the ratified pipe surface is
   whole: connection (unit 1), one-shot pair (2), waitability (3),
   fused paths (3.5), handles-in-messages + delegation (4), the
   one-shot discipline (4.5), driver-as-service three-process real
   (5). The section reads today as ratified-text-plus-seven-unit-
   annotations; the sweep rewrites it as the built thing, with the
   unit history compressed to citations.
2. **§2.2 waitable list closes** — all four pipe kinds + inlet room
   level + the delivery-payload record + consuming attach + the
   completion-queue attachment; persistent-by-default (ruling 9's
   amendment) stated once, in place.
3. **§11 refresh** (design 11 §11's rescope note carries the still-
   parked F2 nod — carry it forward untouched, flag it in the
   As-built if §11's text collides with it).
4. **The M4 recap** — the milestone paragraph: what M4 set out
   (sawlang#232's ladder as amended), what landed (units 0-5 + 4.5
   + 16-stats), the trap ladder 3→2→1 with unit 5's answered/
   discarded refinement, and the standing tail unchanged.
5. **The design-10 audit**: every ruling (1-12) traced to its
   landed doc line; any promised-but-missing doc is the sweep's
   real work list (the As-built records the trace).
6. **The consistency grep** (design 11's method): stale spellings —
   `WouldBlock` as an Ok-channel answer, `Ok(None)` at resolve,
   minted one-shot siblings, `Waiter.give` phrasing vs consuming
   attach, MAX_PROCESSES=2 relics, "one trap per message" without
   the discard caveat — swept across spec.md, README, CLAUDE.md,
   kernel doc banners; each hit fixed or argued in place.

## What this unit does NOT do

No code, no transcript movement (the gate must be byte-identical
both arches), no ABI or numbering edits, no backlog closure, no M5
content beyond the recap's pointer to designs/019. The
`PipeRequestRight.Reply` coverage hole stays a backlog item — a doc
sweep does not write tests.

## The proof

`make sos-test` byte-identical; the As-built carries the audit
trace, the grep catch list in full (design 11's tradition), and the
M4-CLOSES statement the lead moves on at integration.

---

# As-built (M4 unit 6, Sep 3 2026) — SHIPPED

Base `0f73b45` (the design-23 integration). **DOCS ONLY, AND THE GATE IS
BYTE-IDENTICAL.** 116 cases per architecture, 232 runs, riscv32 and arm64
both. The check was the whole 483-line transcript and not a row count: a
baseline was captured at the merge base before a character was edited, `diff`
against it is EMPTY, `cmp` says identical, and both files hash to
`3f6dde15f0f9e3f3cea88bcd3976902414028054c323c96afea74b0460add307`. None of
the three timing-tolerant cases (`thread_preempt`, `timer_interval`,
`process_stats`) flapped, so the tolerance never had to be invoked. sawlang
HEAD `46eebb36` at dispatch and unchanged at the gate; `sawlang.pin`
untouched.

A second, independent proof that nothing could have moved, the one design 11
established: `git diff` over `kernel/ hal/ rt/` filtered to non-comment lines
is EMPTY. Every changed line in a compiled file is a `//`, `///`, `//!` or
`/* */` line, and every doc block still documents the declaration it
documented before — no marker moved, no declaration moved, so design 121's
placement rules were never in play.

## 1. THE M4-CLOSES STATEMENT

**M4 IS COMPLETE AT THIS UNIT'S INTEGRATION.** Every rung of `designs/010`'s
ladder as amended has landed and is recorded: unit 0 (`012`), unit 1 (`013`),
unit 2 (`014`), unit 3 (`015`), `ProcessOp.Stats` (`016`), unit 3.5 (`017`),
unit 4 (`018`), unit 4.5 (`022`), unit 5 (`021`), and unit 6 (this file), with
two riders that touched no kernel surface — the ESP32-C3 board smoke (`020`,
non-gating) and the riscv32 HAL consolidation (`023`). The standing tail
`designs/010` excluded came out of the milestone unchanged. The lead moves on
this statement at integration; M5's anchor is `designs/019-m5-memory-story.md`
and nothing in this sweep touches it beyond that pointer.

## 2. The design-10 audit — every ruling, traced

Twelve rulings, each traced to the doc line that carries it. TEN were already
landed by the units that executed them. **RULING 12 IS WHERE THE AUDIT PAID
OFF** — five promised-but-missing sites, four of them contradicting text in
the same file.

| # | ruling | landed at | verdict |
|---|---|---|---|
| 1 | `SosStatus.Revoked`, the free arm WAKES | §2.2's "THE WAITER ITSELF CAN GO"; §2's `Waiter` row; §11's struck parked-thread entry | LANDED |
| 2 | the peer-gone doctrine | §2.2's "FIRST INSTANCE" paragraph; §2.1's peer-closed bullet; §2's `Pipe` row | **ONE MISS, FIXED** |
| 3 | the factory is `ProcessOp.PipeCreate` | §2's `Pipe` row; §2.1's connection bullet; §11 | LANDED |
| 4 | `post` is the ONE primitive + the fused rider + the struck wrappers | §2.1's `Call` / no-wrapper / `ReplyWait` bullets; §5.7's unit-3.5 op growth | **ONE MISS, FIXED** |
| 5 | staged handles — TAKE-AT-POST as re-ruled | §2's counted-kinds addendum; §2.1's half-a-move bullet; §11's quota entry | **THREE MISSES, FIXED** |
| 6 | 128 B / 4 handles / 2 × `MAX_THREADS` as build defines | §2.1's ring bullet; §2's `Pipe` row | **ONE MISS, FIXED** |
| 7 | (a) driver-as-service (b) keep-mask taken, event rewrite deferred | §2.1's driver-as-service bullet; §11's M4 recap; §2's `Process`/`System` rows | LANDED |
| 8 | the inlet's ROOM-TO-POST waitable level | §2.2's waitable list; §2.1's waitability bullet | **ONE MISS, FIXED** |
| 9 | one-shot attachments; persistent-BY-DEFAULT | §2.2's one-shot bullet + the ratified attach clause | **TWO MISSES, FIXED** |
| 10 | references govern lifetime; teardown writes off | §11's quota entry's write-off paragraph | LANDED (one design-record finding, F1) |
| 11 | delivery carries the payload, (a)-(d) | §2.2's four bullets; §2.1's delivered-reply and delivery-is-the-take bullets; §2's `Waiter` row | **ONE MISS, FIXED** |
| 12 | the one-shot discipline, (a)-(c) | §2.1's discipline bullet; §11's M4 recap; §2's `Pipe` row | **FIVE MISSES, FIXED** |

The misses, named, because they are this unit's real work list:

- **Ruling 2** — §2.2 said "Its other two instances are M4's and are specified
  in `designs/010`". Both are BUILT and spec.md specifies them; the sentence
  sent a reader to a design sketch for shipped behaviour. Now "are BUILT
  (§2.1)".
- **Ruling 4** — §11's "what that plan settles and this section WILL INHERIT"
  paragraph still said blocking `send` / `send(timeout:)` are LIBRARY
  compositions. The user STRUCK the wrappers at unit 3's review and unit 3.5
  made `send(msg)` the fused `Call`. The paragraph went with the accreted
  bullet it lived in; the M4 recap states the built shape.
- **Ruling 5** — THREE sites promising what take-at-post built: §2.5's "the
  SENDABLE-OVER-PIPES half is M4's ... until then a region reaches a second
  process through `give`", §11's M3-memory twin "a region reaching a RUNNING
  process is M4 IPC's", and §11's M3-give twin "dynamic transfer to a RUNNING
  process is M4 IPC's". All three flipped, each naming unit 4.
- **Ruling 6** — §11's pin said the limits were "none of it built until M4
  lands the Pipe". Struck and recorded CLOSED BY M4 — and corrected on a
  detail the ruling itself got half right: `PIPE_BODY_BYTES` and
  `PIPE_MSG_HANDLES` are in `kernel/abi/` (a caller sizes buffers against
  them), not in `kcore.limits` where the ruling put all three.
- **Ruling 8** — §2.2's waitable list collapsed the two endpoint kinds into one
  "Pipe (readable / room-to-post)" entry while claiming ALL EIGHT ARE BUILT, so
  the list did not name its own members and the inlet was a parenthetical
  rather than a waitable. All eight now named individually.
- **Ruling 9** — persistent-BY-DEFAULT was stated in §2.2's ratified attach
  clause and contradicted in two other places: §10's `HandlerGroup` bullet
  ("attachments are persistent subscriptions") and §5's struck item 7
  ("persistent + remove"). Both now carry the amendment and point at §2.2.
- **Ruling 11** — §2.2's delivery-as-take bullet claimed ONE TRAP PER MESSAGE
  flat. Unit 5 refined that to a message the server ANSWERS. Fixed there and at
  `kcore.dispatch`'s `ReplyWait` docstring.
- **Ruling 12 — the five:**
  1. **§5.7 carried NO record of `PipeReplyOp.Ready`**, though ruling 12(b)
     names §5.7 as where the op is renumberable and design 22's own docs-owed
     list asked for the §5-era row. §5.7 has op-growth records for units 3, 3.5
     and 4 and simply stopped. WRITTEN: a 22-line paragraph beside them — the
     no-new-right argument (`Wait`, on `ProcessRight.Wait`'s precedent), the
     contract that changed where a number did not (`Resolve` parks now and its
     wire statuses are identical), and 12(a) read from the other end (the typed
     tier moved where the ABI did not).
  2. **§2's `Pipe` row** still carried SL-16's sentence "`PipeReply.resolve`
     deliberately does NOT [carry `consumes`], because an `Ok(None)` poll
     consumes nothing" — contradicted two thousand words later in the SAME ROW
     by that row's own unit-4.5 block. Rewritten as history plus the flip.
  3. `kernel/abi/src/lib.saw`, `PipeInletOp`: "the typed tier answers
     `Ok(None)` for the first".
  4. `kernel/sysapi/src/pipe.saw`, `PipeHandles`: "That is the property a poll
     loop needs: `Ok(None)` must not cost a caller its capabilities."
  5. TWO KERNEL COMMENTS still described a MINTED ONE-SHOT SIBLING as a live
     actor — `kcore.objects`' `ExchangeState.Settled` ("a `Reply` through a
     minted sibling is `PeerClosed` too") and `kcore.dispatch`'s Settled arm
     ("a second-acting minted sibling") — after 12(c) took `Mint` out of both
     one-shot default sets and monotonic attenuation made the absence
     permanent. Both rewritten to name the ONE name and record the drop.

## 3. Sections as landed, with before/after line counts

**§2.1 — FLIPPED TO BUILT (301 -> 270 lines of built block; the section
385 -> 359).** The RATIFIED text is untouched: Jul 29's four bullets, the
Aug-20 rename and client-API amendment, and the abandonment paragraph stand
exactly as they were, because `designs/010` carries §2.1 by reference as
RATIFIED and this unit has no ruling to reopen it. What was rewritten is
everything below them — the accreted "BUILT WHOLE" block that had grown a
per-unit annotation for each of seven units, read as a changelog, and told a
reader what each unit had done rather than what the thing IS. It is now
organized by WHAT EXISTS, in bullets that run connection -> staging ring ->
peer-closed -> the one-shot pair -> single use -> abandonment -> waitability
-> delivered reply -> `Call` -> the composed timeout -> a fused call mints
nothing -> `ReplyWait` -> the one wake arm -> `send(data?, handles?)` ->
take-at-post -> typed handles at the boundary -> delegation across three
processes -> delivery-as-take -> the one-shot discipline -> driver-as-service
(five sub-bullets) -> the trap ladder -> the spelling note. Unit history is
compressed to a citation in each bullet's first parenthesis; the phrase "unit N
executed this sentence" is gone. Every ratified sentence that has been amended
is amended AT the bullet that amends it, and the two "STILL A PROMISE" markers
died with the block, because there are none left.

**§2.2 — THE WAITABLE LIST CLOSES (the list bullet 17 -> 24 lines).** It named
seven things and claimed eight; it now names its EIGHT members individually —
`PipeOutlet` (readable), `PipeInlet` (room-to-post), Event, Timer, Interrupt,
Process, `PipeReply` (reply-ready), `PipeRequest` (abandoned) — with the
`Wait = 1 << 9` bit and the five per-kind questions named at the site, and a
closing sentence that points at the four bullets below it for what the pipe
arms DELIVER (the payload-carrying record, the consuming attach, the
completion-queue attachment) rather than restating them. Persistent-by-default
is stated ONCE, where it always was: in the ratified attach-semantics clause.
The delivery-as-take bullet gained unit 5's answered-vs-discarded caveat.

**§11 — REFRESHED.** Four edits.
- The ledger header flips: "What the kernel does NOT have after M3" -> **after
  M4**, with "the M3 and M4 rows below are struck as the two ladders landed
  them".
- The **Pipes and PipeReplyHandle** bullet went from **125 lines to 34**. It
  had become a second §2.1 — a per-unit narrative of the whole milestone
  inside the what-is-missing list. It is struck as BUILT and keeps only the
  four facts that are §11's own rather than §2.1's: the ledger answering the
  structural question at both levels, `kcore.wake` ceasing to exist (the module
  list is one seam shorter), the wake dispatch's one arm never becoming a
  second protocol, and agenda 7b's keep mask closing design 11's F1.
- A **65-line M4 RECAP** joins the roadmap beside "M3 IS DONE": what the
  milestone set out (sawlang#232's "explicitly out" seed, ruled Aug 30 as a
  seven-rung ladder over a seven-item agenda), how it was AMENDED while it ran
  (rulings 8-12 all post-date the plan, ruling 5 was re-ruled outright, and
  three of them moved the ladder), the LEDGER one line per rung naming its
  design, the two non-pipe riders, the gate at close (116/arch, 232 runs;
  fifteen object kinds where M3 had eleven), **the trap ladder THREE -> TWO ->
  ONE with unit 5's refinement**, the finale, and the standing tail unchanged
  with `designs/019` as M5's anchor.
- The roadmap arrow gains M4-as-done and the M5 pointer; the M3-close line
  records that M4 built out the one §2 row it left; the Priorities bullet
  flips to past tense.

**§5.7 — the ruling-12 op growth (+22 lines).** The audit's own finding; see §2.

**§12 — two corrections (+3 and +9 lines).** The boot-set bullet promised
"M4's dynamic loading", which M4 never did; and the v1 protocol conventions
now record that after M4 every MECHANISM the bootstrap-pipe/name-service
convention needs exists (create, give under a tag, drain, call, and a message
that carries the handles an answer consists of) and that what is missing is
only the NAME. No convention was invented and no M5 content added.

**README.md (+20/-14) and CLAUDE.md (+14/-7)** — see the catch list.

## 4. The consistency grep — THE FULL CATCH LIST

**Twenty-four prose sites and five source doc comments, all fixed, plus a
66-site path family swept wholesale.**

Prose (spec.md unless noted):

1. §2's `Pipe` row — SL-16's `Ok(None)` sentence (ruling 12 audit).
2. §2's kind count — **"Thirteen of these kinds exist today"** in a sentence
   that LISTS FIFTEEN, one paragraph above "AND TWELVE OF THE FIFTEEN ARE
   COUNTED". M4 unit 2 added the one-shot pair to the list and to the counted
   table and did not bump the headline. Now fifteen.
3. §2.2 — "Its other two instances are M4's and are specified in
   `designs/010`".
4. §2.2 — the waitable list naming seven things and claiming eight.
5. §2.2 — "reaches ONE TRAP PER MESSAGE" without the discard caveat.
6. §2.5 — "The SENDABLE-OVER-PIPES half is M4's ... until then".
7. §5 item 7 (struck) — "persistent + remove".
8. §10 — "attachments are persistent subscriptions".
9. §11's pin — "none of it built until M4 lands the Pipe", plus the
   `kcore.limits`-for-all-three location error.
10. §11's ledger header — "does NOT have after M3".
11. §11's M3 give bullet — "dynamic transfer to a RUNNING process is M4 IPC's".
12. §11's M3 memory bullet — "a region reaching a RUNNING process is M4 IPC's".
13. §11's IntrSpinLock bullet — **"SMP waits for pipes"**. Pipes landed; SMP is
    in the standing tail by ruling, so what it waits for is a milestone that
    takes the tail up.
14. §11's M3-close line — "with Pipe the one row of §2 left and M4's".
15. §11's Priorities bullet — "M4 does not take it up either", present tense
    about a finished milestone.
16. §11's roadmap arrow — M4 as a future plan of record.
17. §11's Pipes bullet — ruling 4's struck library wrappers (see §2).
18. §12's boot-set bullet — "for M4's dynamic loading".
19. §12's v1 protocol conventions — nothing recording what M4 made possible.
20. README line 4 — "today on QEMU's `virt` boards", with design 20's C3 smoke
    unmentioned.
21. README line 8 — the object list omitted memory, mappings and pipes: every
    kind M3 and M4 built.
22. README line 38 — **"40 cases per architecture"**, the M1b number. The gate
    is 116 per architecture, 232 runs.
23. README line 46 — the PRE-CONSOLIDATION hal layout (design 23 §8.4's named
    leftover 1).
24. CLAUDE.md's repo map — the same, with `hal/riscv32-esp32c3/` missing
    entirely (design 23 §8.4's named leftover 2). Both now show
    `hal/riscv32-common/` as the `rv32core` ARCH half with the two riscv32
    BOARDS under it, and arm64 as the one directory it still is.

Source doc comments (five, each a ruling-12 survivor — see §2 for the text):

25. `kernel/abi/src/lib.saw`, `PipeInletOp`.
26. `kernel/sysapi/src/pipe.saw`, `PipeHandles`.
27. `kernel/core/objects.saw`, `ExchangeState.Settled`.
28. `kernel/core/dispatch.saw`, the `Settled` resolve arm.
29. `kernel/core/dispatch.saw`, `pipe_reply_wait`'s docstring — "one trap per
    message" without the discard caveat.

**AND THE PATH FAMILY: 66 sites across 21 files.** Every doc banner and ABI.md
under `kernel/`, `hal/` and `rt/` still spelled the PRE-SPLIT prefix —
`sos/kernel/`, `sos/hal/`, `sos/rt/`, `sos/root/`, `sos/tests/`, `sos/spec.md`
— from before the tree flattened at the sawos split (sawlang#238 D-e, Aug 28).
Design 11's catch 14 swept the twenty-one such paths in spec.md and left this
half; a reader following `sos/hal/arm64/kernel/` out of
`hal/riscv32/kernel/ABI.md` finds nothing. Corrected wholesale by prefix strip,
verified line by line, and every touched line is a comment or an `.md` line:

```
  4 hal/arm64/kernel/ABI.md              3 kernel/abi/src/lib.saw
  4 hal/arm64/kernel/lib.saw             4 kernel/core/lib.saw
  1 hal/arm64/kernel/sink.c              1 kernel/core/time.saw
  8 hal/arm64/user/ABI.md                3 kernel/main.saw
  1 hal/arm64/user/root.ld               2 kernel/sysapi/src/lib.saw
  3 hal/arm64/user/syscall.c             2 kernel/sysapi/src/rt.saw
  3 hal/riscv32-common/user/syscall.c    3 rt/common/src/lib.saw
  3 hal/riscv32-esp32c3/kernel/lib.saw   2 rt/common_c/support.c
  1 hal/riscv32-esp32c3/user/root.ld
  6 hal/riscv32/kernel/ABI.md
  3 hal/riscv32/kernel/lib.saw
  8 hal/riscv32/user/ABI.md
  1 hal/riscv32/user/root.ld
```

### CHECKED AND LEFT ALONE, so the reader knows they were looked at

- `kernel/sysapi/src/pipe.saw`'s module banner and `resolve_into_frame`, and
  `kernel/sysapi/src/floor.saw`'s poll-split banner and `polled_ok`: all four
  name `Ok(None)` or `polled_value` as HISTORY and state the flip in the same
  breath. Correct as written — these are the sites design 22 itself swept.
- `kernel/abi/src/lib.saw`'s `PipeReplyRight` and `pipe_reply_rights()`, and
  `PipeReply.mint`'s docstring: all three already record ruling 12(c) in full,
  `mint` being kept deliberately as the rule's witness.
- `kernel/core/refs.saw`'s "a release with a minted sibling still live", §2's
  `Waiter` row and §11's revocation entry: those are WAITER siblings, and a
  Waiter keeps `Mint`.
- §2.1's "minted sibling INLETS": endpoints keep `Mint` by design 22's own
  out-of-scope list — per-connection attenuable authority is the design.
- `kernel/core/sched.saw`'s two `MAX_PROCESSES`-at-two notes: already corrected
  by design 21, and they read as history ("untaken by topology rather than by
  the table's size"), which is accurate.
- The three `(M4+)` markers (§2.5 twice, §11's DMA-confinement bullet): "M4 or
  later" is still literally TRUE — M4 declined all three by ruling — and
  re-spelling them M5+ would pre-empt the M5 scoping session this brief
  forbids. ARGUED, left.
- `tests/` comments naming `Ok(None)` (`pipe-basics`, `pipe-oneshot`,
  `pipe-child`, `pipe-delegate` — 8 sites): OUT of edit scope by the brief's
  own no-test-edits rule. RECORDED here for whoever next touches them.
- `tools/sos_runner.py` (7 sites), `root/Saw.toml` and `rt/common/Saw.toml`
  (2 sites) carry the pre-split `sos/` prefix too: runner and manifests are
  excluded by the hard rules. RECORDED, not touched.
- Roughly forty other "will"/"remains" hits in doc comments: ordinary future
  tense about runtime behaviour, not stale promises.

## 5. Deviations, argued

**D-1 — the path sweep is 66 sites, which is more than a docs sweep's usual
mandate.** Argued exactly as design 11 argued its twenty-one-site spec.md
twin, and for the stronger reason that this IS that catch's other half: the
consistency pass exists to catch what per-unit edits could not, a directory the
document names that does not exist is a stale fact of exactly that kind, and
the whole change is a prefix strip that was diffed line by line and proved to
touch no non-comment line in any compiled file.

**D-2 — §2.1's RATIFIED text was NOT rewritten.** The brief's item 1 describes
the section as "ratified text + seven units of annotations" and asks for the
rewrite; what was rewritten is the ANNOTATIONS. `designs/010` opens by saying
§2.1 is RATIFIED and carried whole, and this unit has no ruling to reopen it —
so every amendment the ratified sentences have taken (the pair naming the
"per-client connection object", the ring amending "one fixed slot",
abandonment re-wording to the last reference, `send` becoming a kernel call) is
stated in the built block AT the bullet that amends it, and the ratified
paragraphs are byte-identical.

**D-3 — two sections outside the brief's list were edited.** §5.7 gained the
ruling-12 op record, which is the audit's own finding rather than an addition
(ruling 12(b) names §5.7 as the op's home), and §12 gained two corrections the
grep found. Both are the consistency pass's mandate; neither adds a section.

**D-4 — the M4 recap sits in §11's roadmap, after the M3 group rather than
before it.** The roadmap list is chronological (M0, M1, M1b, M2, M3, then M3's
two trailing unit entries), so M4 goes last. The pointer a reader arrives on is
the roadmap ARROW at the top of that bullet, which now reads
"... -> M4 pipes (... CLOSED by design 24) -> M5, whose anchor is
`designs/019`".

## 6. Findings for the lead

**F1 — `designs/010` ruling 10 still argues its unreachability from
`MAX_PROCESSES = 2`.** The clause reads "UNREACHABLE TODAY, and provably
(MAX_PROCESSES = 2, give flows only downward, root dies last)". Design 21
made it THREE, and the kernel's own two comments at that arm were corrected
then — `kcore.sched` now says the branch is "untaken by topology rather than by
the table's size", which is the honest version. The DESIGN RECORD was not, and
a ruled design record is out of a docs unit's edit scope (design 11's F3, same
shape). One sentence for the lead, or a rider on whatever next reads ruling 10.

**F2 — the parked nod is CARRIED FORWARD UNTOUCHED, and it COLLIDES with the
refreshed §11 in one place.** §11's physical-region-refcount pin (§5.9) and its
"It lands with a real allocator (M4+)" are exactly as design 11 left them; not
a word moved. THE COLLISION: the bullet's header reads "**Orchestrator pins
STILL OPEN, restated at M3's close**", and it now sits in a §11 whose ledger
closes M4, so the label is a milestone behind. I did not re-word it — the pin's
wording is the parked question's own subject, and re-labelling the bullet
touches the nod. The MESSAGE-LIMITS clause in that same bullet WAS closed (it
is not the nod's subject and it was plainly false), and it is written as
"~~Also open~~ **CLOSED BY M4**" precisely so the M3-close header stays true of
what remains open under it. If the lead wants the header current, the minimal
edit is "restated at M3's close and unmoved at M4's" — but that is the lead's
call, because the next thing after this sweep is the M5 scoping session, which
is where F2's own recommendation (re-word the pin, or close it with "byte
accounting and pool returns, M5+") naturally lands.

**F3 — `designs/todo.md`'s backlog still calls design 10 a DRAFT.** The line
reads "M4 scoping — pipes [#10 — designs/010-m4-pipes.md, DRAFT Aug 30,
awaiting user review]". It was RULED the same day and has been the plan of
record for the entire milestone. The backlog is frozen by this brief (no
closing backlog items), so it is flagged rather than fixed — and it will read
oddly the moment M4 closes, since the entry describes work that is finished.

**F4 — the spec disagreed with itself about how many object kinds exist, one
paragraph apart.** "**Thirteen** of these kinds exist today" introduced a list
of fifteen and sat directly above "AND **TWELVE OF THE FIFTEEN** ARE COUNTED".
M4 unit 2 added `PipeReply` and `PipeRequest` to the list and to the counted
table and left the headline at the unit-1 number. Nothing downstream read the
wrong number — the counted table is the load-bearing one and it was right —
but it is the sharpest catch of the sweep for design 11's own reason: two
numbers in one document, one paragraph apart, disagreeing.

**F5 — no SL entry is owed.** This unit wrote prose. Every source edit stayed
inside an existing `//`, `///`, `//!` or `/* */` block with its documented
declaration unmoved; no declaration, marker or code token moved anywhere, and
no language deficiency was met.
