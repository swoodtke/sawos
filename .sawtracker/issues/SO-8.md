---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":4181,"created":"1788791282","id":"SO-8","labels":["todo-import","backlog","deferred","decision-required","design","plan","design-proposal"],"priority":"low","project":"SO","revision":1,"sequence":8,"status":"open","title":"Revisit lazy PipeMsg handle decoding only at the tight-SRAM trigger","updated":"1788791282"}
---


## Description

## Scope and status

Revisit lazy PipeMsg handle decoding only at the tight-SRAM trigger

Imported from repository TODO records on 2026-09-07. The reporter/version, evidence, workarounds and prior rulings are preserved in the source context below. This import is not a fresh reproduction or approval of a proposed design.

Scheduling: retain the source’s deferral or revisit trigger.

## Proposed plan

1. Reconcile the linked brief and recorded rulings with current consumers. Keep already-landed behavior and stated deferrals explicit.
2. Draft the remaining contract: supported syntax/API, ownership and failure behavior, alternatives, compatibility effects and the exact questions still requiring a decision.
3. Define small implementation units and consumer migrations, then specify the positive, negative and boundary tests before dispatch.
4. Name any permitted transcript/image-size changes before implementation and validate the affected riscv32/arm64 QEMU cases; preserve capability attenuation and failure behavior.

## Acceptance criteria

- [ ] The design names a concrete consumer or retains its recorded revisit trigger.
- [ ] Existing rulings are preserved; unresolved choices and a recommended option are explicit.
- [ ] The proposed API/semantics, migration scope and acceptance tests are reviewable. Drafting this plan does not mark an unruled design approved.

## Existing design references

- [sawos/designs/018-rendezvous.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/018-rendezvous.md)
- [sawos/designs/025-m5-sketch.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/025-m5-sketch.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 133–165

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L133-L165)

- LAZY HANDLE DECODE in `PipeMsg` — the message vocabulary's image cost
  [#18 As-built §12, Sep 2]: unit 4's transcript accounting measured
  +26.7% (riscv32) / +22.6% (arm64) across every image that names the
  pipe or wait surface, and the cause is the TYPED value rather than the
  wire record — `PipeMsg` grew from `{len, bytes}` to
  `{len, [PipeHandle?; 4], bytes}`, and a `PipeHandle` is an enum of six
  `NoCopy` wrappers, so every take/resolve/wait caller moves a bigger
  value and links drop glue for four optional six-way enums. It lands
  hardest where it is least deserved: `tests/event-wake` waits on an
  Event, touches no pipe, and grew 5,584 B / 8,192 B, because
  `decode_wait` (4,934 B on riscv32), `wait_message` (962 B) and
  `decode_handle` (568 B) link into EVERY image that calls `wait`
  whether or not its attachments could produce a message. The lever:
  keep the kind bytes and the handle WORDS in `PipeMsg` and construct a
  wrapper only when the receiver asks for a slot
  (`msg.take_handle(0) -> PipeHandle?`), which moves the six-way
  construction out of `decode_wait` into a function only a
  handle-reading program links; the drop then walks words rather than
  matching enums. No kernel and no ABI consequence — it is entirely
  inside `sos.pipe`. NOT done in unit 4 because it rewrites the
  RECEIVING VOCABULARY that unit's §API was user-reviewed on, and the
  ergonomics trade (a match on an optional field becomes a call then a
  match) is a taste question the lead should answer

  **DISPOSITION (user, Sep 2): deliberately UNSCHEDULED** — the M5
  namespace makes handle-receipt universal (open() answers
  capabilities in messages), and XIP on ESP32-class parts lands the
  code tax on flash, not SRAM. Revisit trigger: a genuinely
  tight-SRAM tier target; if it fires, prefer sawc-side drop-glue
  dedup BEFORE this API rewrite. **[RIDER, Sep 3, the design-25
  session: the "M5 namespace" premise is STALE — the namespace was
  ruled OUT of M5 (designs/025 seed 3). The disposition itself
  stands on its other two legs (XIP + the revisit trigger).]**


## Comments

