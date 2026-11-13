// The ESP32-S3 board's register-holding spin: `sos_frame_hold`, for the
// `frame-hold` case. Assembly, because no compiled code can say which
// register holds what across a loop; in a C file as one file-scope `asm`, so
// the integrated assembler takes it with the rest of the package's C.
//
// u32 sos_frame_hold(u32 *mine, u32 *other, u32 rounds, u32 seed,
//                    u32 other_seed)
//
// Each round loads a value derived from `seed` and the round into every
// register it holds, publishes the round in `*mine`, then spins until
// `*other` has passed the round, and checks each held register against what
// it loaded. The spin makes no call and no syscall, so it ends only when a
// tick has preempted this thread and the other has run. It answers 0 after
// `rounds` rounds, or on the first mismatch the register's code:
//
//   1 a0, n + 1 for an (a2 to a15), 20 SAR, 21 SCOMPARE1, 22 THREADPTR,
//   23 LBEG, 24 LEND, 25 LCOUNT
//
// and then writes 0x7FFFFFFF to `*mine`, so the other thread's spins end.
//
// Held: a0, a2 to a13, SAR, SCOMPARE1, THREADPTR, LBEG, LEND, LCOUNT, and one
// of a14 and a15 in turn. Not held: a1, the stack pointer every load here
// goes through.
//
// The spin needs one register to load the other thread's word into, so a14
// and a15 take turns: in an even round a15 holds the round's counter and a14
// is the scratch, in an odd round the other way about. The counter is the
// round plus this thread's bias, (seed >> 28) << 16, so the two threads'
// counters differ; it is what the spin compares the other's word against, it
// is checked against the value the round implies on every pass of the spin
// and once more when the spin ends, and it is loaded before the round is
// published. The thread that publishes first in a round cannot leave its
// spin until the other has published, so it is preempted with the counter
// held: every round tests the restore of one of the two registers. Each
// thread publishes round + 1 plus the other's bias, which is what the other
// thread's counter is compared with.
//
// LCOUNT is non-zero for the whole round, so a pass through the address in
// LEND would loop back to LBEG. Both point into `.Lhold_dead`, a block after
// the function that nothing executes, so the loop hardware never acts and
// the three registers hold whatever was written. They are cleared, and
// THREADPTR restored, before the function returns.
//
// The loop registers are reached through `loop_rsr` and `loop_wsr`, which
// encode RSR and WSR by hand (`0x03 SR T<<4` and `0x13 SR T<<4`, low byte
// first; LBEG is 0, LEND 1, LCOUNT 2): the package's C is built for the
// triple's default core, whose assembler does not know the loop option.

__asm__(
"    .section .text.sos_frame_hold, \"ax\"\n"
"    .literal .Lhold_dead_lit, .Lhold_dead\n"
"    .align 4\n"
"    .global sos_frame_hold\n"
"    .type sos_frame_hold, @function\n"
"    .macro  loop_rsr t, sr\n"
"    .byte   (\\t << 4), \\sr, 0x03\n"
"    .endm\n"
"    .macro  loop_wsr t, sr\n"
"    .byte   (\\t << 4), \\sr, 0x13\n"
"    .endm\n"
"sos_frame_hold:\n"
"    addi    a1, a1, -128\n"
"    addi    a1, a1, -16\n"
"    s32i    a0, a1, 0\n"
"    s32i    a12, a1, 4\n"
"    s32i    a13, a1, 8\n"
"    s32i    a14, a1, 12\n"
"    s32i    a15, a1, 16\n"
"    rur.threadptr a14\n"
"    s32i    a14, a1, 20\n"
"    s32i    a2, a1, 24\n"           // mine
"    s32i    a3, a1, 28\n"           // other
"    s32i    a4, a1, 32\n"           // rounds
"    s32i    a5, a1, 36\n"           // seed
"    extui   a14, a6, 28, 4\n"
"    slli    a14, a14, 16\n"
"    s32i    a14, a1, 44\n"          // the other's bias
"    movi    a14, 0\n"
"    s32i    a14, a1, 40\n"          // round
"\n"
// The round's values, into the table at 48: a0, a2 to a13 (48 to 96), then
// SCOMPARE1 (104), THREADPTR (108) and LCOUNT (120), each a step past the
// last from seed + (round << 16). SAR (100) is the low six bits of seed +
// round, which is all it holds. LBEG (112) and LEND (116) are in the dead
// block, at an offset taken from the seed's top nibble.
".Lhold_round:\n"
"    l32i    a14, a1, 40\n"
"    slli    a15, a14, 16\n"
"    l32i    a13, a1, 36\n"
"    add     a15, a15, a13\n"
"    .irp    off, 48,52,56,60,64,68,72,76,80,84,88,92,96,104,108,120\n"
"    s32i    a15, a1, \\off\n"
"    addmi   a15, a15, 0x700\n"
"    addi    a15, a15, 0x13\n"
"    .endr\n"
"    add     a15, a13, a14\n"
"    extui   a15, a15, 0, 6\n"
"    s32i    a15, a1, 100\n"
"    extui   a15, a13, 28, 4\n"
"    slli    a15, a15, 2\n"
"    l32r    a14, .Lhold_dead_lit\n"
"    add     a14, a14, a15\n"
"    s32i    a14, a1, 112\n"
"    addi    a14, a14, 64\n"
"    s32i    a14, a1, 116\n"
// The counter (124) and the word to publish (128).
"    l32i    a14, a1, 40\n"
"    extui   a15, a13, 28, 4\n"
"    slli    a15, a15, 16\n"
"    add     a15, a15, a14\n"
"    s32i    a15, a1, 124\n"
"    l32i    a15, a1, 44\n"
"    add     a15, a15, a14\n"
"    addi    a15, a15, 1\n"
"    s32i    a15, a1, 128\n"
"\n"
// Load the special registers, LCOUNT last so the loop bounds are in place
// first; then, by the round's parity, the counter, the publish, the general
// registers and the spin.
"    l32i    a14, a1, 100\n"
"    wsr     a14, sar\n"
"    l32i    a14, a1, 104\n"
"    wsr     a14, scompare1\n"
"    l32i    a14, a1, 108\n"
"    wur.threadptr a14\n"
"    l32i    a14, a1, 112\n"
"    loop_wsr 14, 0\n"
"    l32i    a14, a1, 116\n"
"    loop_wsr 14, 1\n"
"    isync\n"
"    l32i    a14, a1, 120\n"
"    loop_wsr 14, 2\n"
"\n"
// `hold_wait held, scratch, code`: hold the counter in `held`, publish, load
// the general registers, and spin while *other <= the counter, checking the
// counter on every pass and at the end. a13 carries the published word and
// is loaded from the table after it.
"    .macro  hold_wait held, scratch, code\n"
"    l32i    \\held, a1, 124\n"
"    l32i    \\scratch, a1, 24\n"
"    l32i    a13, a1, 128\n"
"    s32i    a13, \\scratch, 0\n"
"    l32i    a0, a1, 48\n"
"    l32i    a2, a1, 52\n"
"    l32i    a3, a1, 56\n"
"    l32i    a4, a1, 60\n"
"    l32i    a5, a1, 64\n"
"    l32i    a6, a1, 68\n"
"    l32i    a7, a1, 72\n"
"    l32i    a8, a1, 76\n"
"    l32i    a9, a1, 80\n"
"    l32i    a10, a1, 84\n"
"    l32i    a11, a1, 88\n"
"    l32i    a12, a1, 92\n"
"    l32i    a13, a1, 96\n"
"1:\n"
"    l32i    \\scratch, a1, 124\n"
"    bne     \\scratch, \\held, 2f\n"
"    l32i    \\scratch, a1, 28\n"
"    memw\n"
"    l32i    \\scratch, \\scratch, 0\n"
"    bgeu    \\held, \\scratch, 1b\n"
"    l32i    \\scratch, a1, 124\n"
"    beq     \\scratch, \\held, 3f\n"
"2:\n"
"    movi    a2, \\code\n"
"    j       .Lhold_fail\n"
"3:\n"
"    .endm\n"
"    l32i    a14, a1, 40\n"
"    bbci    a14, 0, .Lhold_even\n"
"    j       .Lhold_odd\n"
".Lhold_even:\n"
"    hold_wait a15, a14, 16\n"
"    j       .Lhold_checks\n"
".Lhold_odd:\n"
"    hold_wait a14, a15, 15\n"
".Lhold_checks:\n"
"\n"
"    .macro  hold_check reg, off, code\n"
"    l32i    a14, a1, \\off\n"
"    beq     a14, \\reg, 1f\n"
"    movi    a2, \\code\n"
"    j       .Lhold_fail\n"
"1:\n"
"    .endm\n"
"    .macro  hold_check_sr read, off, code\n"
"    \\read\n"
"    l32i    a14, a1, \\off\n"
"    beq     a14, a15, 1f\n"
"    movi    a2, \\code\n"
"    j       .Lhold_fail\n"
"1:\n"
"    .endm\n"
"    hold_check a0, 48, 1\n"
"    hold_check a2, 52, 3\n"
"    hold_check a3, 56, 4\n"
"    hold_check a4, 60, 5\n"
"    hold_check a5, 64, 6\n"
"    hold_check a6, 68, 7\n"
"    hold_check a7, 72, 8\n"
"    hold_check a8, 76, 9\n"
"    hold_check a9, 80, 10\n"
"    hold_check a10, 84, 11\n"
"    hold_check a11, 88, 12\n"
"    hold_check a12, 92, 13\n"
"    hold_check a13, 96, 14\n"
"    hold_check_sr \"rsr a15, sar\", 100, 20\n"
"    hold_check_sr \"rsr a15, scompare1\", 104, 21\n"
"    hold_check_sr \"rur.threadptr a15\", 108, 22\n"
"    hold_check_sr \"loop_rsr 15, 0\", 112, 23\n"
"    hold_check_sr \"loop_rsr 15, 1\", 116, 24\n"
"    hold_check_sr \"loop_rsr 15, 2\", 120, 25\n"
"\n"
"    l32i    a14, a1, 40\n"
"    addi    a14, a14, 1\n"
"    s32i    a14, a1, 40\n"
"    l32i    a15, a1, 32\n"
"    bgeu    a14, a15, 2f\n"
"    j       .Lhold_round\n"
"2:\n"
"    movi    a2, 0\n"
"    j       .Lhold_out\n"
"\n"
".Lhold_fail:\n"
"    l32i    a14, a1, 24\n"
"    movi    a15, -1\n"
"    srli    a15, a15, 1\n"
"    s32i    a15, a14, 0\n"
"\n"
".Lhold_out:\n"
"    movi    a14, 0\n"
"    loop_wsr 14, 2\n"
"    loop_wsr 14, 0\n"
"    loop_wsr 14, 1\n"
"    isync\n"
"    l32i    a14, a1, 20\n"
"    wur.threadptr a14\n"
"    l32i    a0, a1, 0\n"
"    l32i    a12, a1, 4\n"
"    l32i    a13, a1, 8\n"
"    l32i    a14, a1, 12\n"
"    l32i    a15, a1, 16\n"
"    addi    a1, a1, 16\n"
"    addi    a1, a1, 128\n"
"    ret\n"
"    .size sos_frame_hold, . - sos_frame_hold\n"
"\n"
// Never executed: only addresses for LBEG and LEND.
"    .align 4\n"
".Lhold_dead:\n"
"    .fill 128, 1, 0\n"
);
