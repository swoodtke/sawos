// The register file a thread starts with, on the ESP32-S3. A first thread
// writes one pattern into all 64 physical registers and exits, so its frame
// holds the pattern when its slot is freed; the probe thread is created next,
// into that slot. A filler thread then writes another pattern into all 64,
// starts the probe and joins it, so the probe's first entry follows the
// filler's trap with nothing but the kernel in between. The probe's first
// instructions store every physical register (`regs.S`), and `report` names
// each that is not zero:
//
//   SOS regs: ar[1]=stack top          the probe's stack pointer
//   SOS regs: ar[2]=0x5eed0001         its argument, for a call0 entry
//   SOS regs: ar[4]=0x7ffffffc         a windowed entry's return address
//   SOS regs: ar[6]=0x5eed0001         its argument, for a windowed entry
//   SOS regs: 4 nonzero, 0 unexpected
//
// A register the restore did not load shows the filler's pattern
// (0xf1f1xxxx) or a kernel value; one `frame_init` did not clear shows the
// first thread's (0xf0f1xxxx). The probe exits with the count of
// unexpected registers, the filler shuts the machine down with it, and the
// root never wakes from its join on the filler.

typedef unsigned int u32;

u32 sos_syscall1(u32 handle, u32 op, u32 arg0);
u32 sos_syscall3(u32 handle, u32 op, u32 arg0, u32 arg1, u32 arg2,
                 u32 *value_out);
void probe_entry(u32 arg);
void fill_entry(u32 unused);
void fill_exit_entry(u32 unused);

#define SYS_DEBUG_PRINT    0u
#define SYS_SHUTDOWN       1u
#define SYS_PROCESS_SELF   2u
#define PROC_THREAD_CREATE 0u
#define PROC_THREAD_SELF   1u
#define THREAD_START       0u
#define THREAD_JOIN        1u
#define THREAD_EXIT        2u
#define ALL_RIGHTS         0xFFFFFFFFu
#define RELEASE_OP         0xFFFFu

#define PROBE_ARG          0x5EED0001u
#define WINDOWED_RETURN    0x7FFFFFFCu
#define STACK_WORDS        512u

// Shared with `regs.S`.
u32 g_boot;
u32 g_regs[64];
u32 g_probe_handle;
u32 g_proc;
u32 g_exiter;

static u32 g_probe_top;
static u32 g_stacks[3][STACK_WORDS] __attribute__((aligned(16)));

static void put(u32 c) { (void)sos_syscall1(g_boot, SYS_DEBUG_PRINT, c); }

static void say(const char *s) {
    while (*s) {
        put((unsigned char)*s++);
    }
}

static void hex(u32 v) {
    say("0x");
    for (int shift = 28; shift >= 0; shift -= 4) {
        u32 d = (v >> shift) & 0xF;
        put(d < 10 ? '0' + d : 'a' + d - 10);
    }
}

static void dec(u32 v) {
    if (v >= 10) {
        dec(v / 10);
    }
    put('0' + v % 10);
}

static u32 expected(u32 i) {
    switch (i) {
    case 1:
        return g_probe_top;
    case 2:
    case 6:
        return PROBE_ARG;
    case 4:
        return WINDOWED_RETURN;
    default:
        return 0;
    }
}

// Reached by `call4` from `probe_entry` once `g_regs` holds the registers.
// Ends the probe thread with the count of unexpected registers.
void report(void) {
    u32 nonzero = 0, unexpected = 0;
    for (u32 i = 0; i < 64; i++) {
        u32 v = g_regs[i];
        if (v != expected(i)) {
            unexpected++;
        }
        if (v == 0) {
            continue;
        }
        nonzero++;
        say("SOS regs: ar[");
        dec(i);
        say("]=");
        if (i == 1 && v == g_probe_top) {
            say("stack top");
        } else {
            hex(v);
        }
        put('\n');
    }
    say("SOS regs: ");
    dec(nonzero);
    say(" nonzero, ");
    dec(unexpected);
    say(" unexpected\n");
    u32 me = 0;
    (void)sos_syscall3(g_proc, PROC_THREAD_SELF, 0, 0, 0, &me);
    (void)sos_syscall1(me, THREAD_EXIT, unexpected);
}

static void shutdown(u32 status) {
    (void)sos_syscall1(g_boot, SYS_SHUTDOWN, status);
    for (;;) {
    }
}

void _start(u32 boot) {
    g_boot = boot;
    if (sos_syscall3(boot, SYS_PROCESS_SELF, ALL_RIGHTS, 0, 0, &g_proc) != 0) {
        say("SOS regs: no process handle\n");
        shutdown(1);
    }
    u32 exiter = 0, code = 0;
    if (sos_syscall3(g_proc, PROC_THREAD_CREATE, (u32)fill_exit_entry,
                     (u32)&g_stacks[2][STACK_WORDS], 0, &exiter) != 0) {
        say("SOS regs: thread create FAILED\n");
        shutdown(1);
    }
    (void)sos_syscall1(exiter, THREAD_START, 0);
    (void)sos_syscall3(exiter, THREAD_JOIN, 0, 0, 0, &code);
    // The last reference to the exited thread goes, and its slot, frame
    // included, is free for the probe.
    (void)sos_syscall1(exiter, RELEASE_OP, 0);
    g_probe_top = (u32)&g_stacks[0][STACK_WORDS];
    u32 filler = 0;
    if (sos_syscall3(g_proc, PROC_THREAD_CREATE, (u32)probe_entry,
                     g_probe_top, PROBE_ARG, &g_probe_handle) != 0 ||
        sos_syscall3(g_proc, PROC_THREAD_CREATE, (u32)fill_entry,
                     (u32)&g_stacks[1][STACK_WORDS], 0, &filler) != 0) {
        say("SOS regs: thread create FAILED\n");
        shutdown(1);
    }
    say("SOS regs: a thread fills all 64 registers, then starts the probe\n");
    (void)sos_syscall1(filler, THREAD_START, 0);
    (void)sos_syscall3(filler, THREAD_JOIN, 0, 0, 0, &code);
    say("SOS regs: the root woke, which nothing should make it do\n");
    shutdown(1);
}
