// A windowed process on the ESP32-S3: register-file overflow and underflow
// at each call size, a system call from deep in a chain, `movsp` with the
// caller spilled, and two threads preempted deep in their chains, last of
// all between a call and its callee's `entry` (call8 in one thread, call12
// in the other).
//
//   SOS win: start ps=... wb=... ws=...      the window state `_start` sees
//   SOS win: chain4 ok                       (and chain8, chain12)
//   SOS win: syscall from depth              printed from depth 30
//   SOS win: syscall chain ok
//   SOS win: alloca ok
//   SOS win: threads joined a=0 b=0
//   SOS win: done
//
// A failure prints the code `chains.S` describes and the process shuts the
// machine down with status 1.

typedef unsigned int u32;

u32 sos_syscall1(u32 handle, u32 op, u32 arg0);
u32 sos_syscall3(u32 handle, u32 op, u32 arg0, u32 arg1, u32 arg2,
                 u32 *value_out);
u32 chain4(u32 depth, u32 seed);
u32 chain8(u32 depth, u32 seed);
u32 chain12(u32 depth, u32 seed);
u32 pre_entry8(volatile u32 *mine, volatile u32 *other);
u32 pre_entry12_via(volatile u32 *mine, volatile u32 *other);

#define SYS_DEBUG_PRINT    0u
#define SYS_SHUTDOWN       1u
#define SYS_PROCESS_SELF   2u
#define PROC_THREAD_CREATE 0u
#define PROC_THREAD_SELF   1u
#define THREAD_START       0u
#define THREAD_JOIN        1u
#define THREAD_EXIT        2u
#define ALL_RIGHTS         0xFFFFFFFFu

#define MODE_NONE    0u
#define MODE_SYSCALL 1u
#define MODE_SPIN    2u

#define DEPTH        40u
#define THREAD_DEPTH 24u
#define ROUNDS       6u
#define STACK_WORDS  1024u
#define GIVE_UP      0x7FFFFFFFu

// What a chain of `chains.S` answers on success.
#define ANSWER(seed, depth) ((seed) + ((depth) << 16))

static u32 g_boot;
static u32 g_proc;
static volatile u32 g_mode;
static volatile u32 g_progress[2];
static volatile u32 g_pub[2];
static volatile u32 g_rel[2];
static u32 g_stacks[2][STACK_WORDS] __attribute__((aligned(16)));

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

static u32 read_ps(void) {
    u32 v;
    __asm__ volatile("rsr %0, ps" : "=a"(v));
    return v;
}

static u32 read_wb(void) {
    u32 v;
    __asm__ volatile("rsr %0, windowbase" : "=a"(v));
    return v;
}

static u32 read_ws(void) {
    u32 v;
    __asm__ volatile("rsr %0, windowstart" : "=a"(v));
    return v;
}

static void shutdown(u32 status) {
    (void)sos_syscall1(g_boot, SYS_SHUTDOWN, status);
    for (;;) {
    }
}

// The hook at the bottom of every chain. Spinning: publish this round and
// wait for the other thread's, which only a tick lets run. The role and the
// round are the seed's top nibble and low half. In the last two rounds one
// thread spins between a call and its callee's `entry` instead (thread 0
// after a call8, then thread 1 after a call12), having published that it is
// there, and the other releases it once it has seen that: so the spinner is
// preempted at that point whichever thread arrives first.
u32 chain_hook(u32 seed) {
    u32 round = seed & 0xFFFF;
    if (g_mode == MODE_SPIN && round >= ROUNDS) {
        u32 role = (seed >> 28) - 1;
        u32 k = round - ROUNDS;
        if (role == k) {
            u32 r = k == 0 ? pre_entry8(&g_pub[k], &g_rel[k])
                           : pre_entry12_via(&g_pub[k], &g_rel[k]);
            return r == ((u32)&g_pub[k] ^ (u32)&g_rel[k]) ? 0 : 0x800000FCu;
        }
        while (!g_pub[k]) {
        }
        g_rel[k] = 1;
        return 0;
    }
    if (g_mode == MODE_SYSCALL) {
        say("SOS win: syscall from depth\n");
        u32 proc = 0;
        u32 st = sos_syscall3(g_boot, SYS_PROCESS_SELF, ALL_RIGHTS, 0, 0,
                              &proc);
        if (st != 0 || proc == 0) {
            return 0x800000FEu;
        }
        u32 me = 0;
        st = sos_syscall3(proc, PROC_THREAD_SELF, 0, 0, 0, &me);
        if (st != 0 || me == 0) {
            return 0x800000FDu;
        }
        return 0;
    }
    if (g_mode == MODE_SPIN) {
        u32 role = (seed >> 28) - 1;
        u32 round = seed & 0xFFFF;
        g_progress[role] = round + 1;
        while (g_progress[1 - role] < round + 1) {
        }
        return 0;
    }
    return 0;
}

static int check(const char *what, u32 got, u32 want) {
    if (got == want) {
        say("SOS win: ");
        say(what);
        say(" ok\n");
        return 0;
    }
    say("SOS win: ");
    say(what);
    say(" FAILED got=");
    hex(got);
    say(" want=");
    hex(want);
    put('\n');
    return 1;
}

// Read through a volatile, so the compiler keeps each value it made before a
// call in a register across the call rather than making it again after.
static volatile u32 g_salt = 0x9E3779B9u;

__attribute__((noinline)) u32 mix(u32 x, u32 k) {
    return (x ^ (k * g_salt)) + k;
}

// `movsp` in a frame whose caller has been spilled: the chain below spills
// `holder` and this frame; the return reloads this frame only, so the alloca
// finds its caller's window gone.
__attribute__((noinline)) u32 alloca_after_spill(u32 n, u32 seed) {
    u32 r = chain8(20, seed);
    volatile u32 *p = __builtin_alloca(n * 4);
    for (u32 i = 0; i < n; i++) {
        p[i] = seed ^ (i * 0x01010101u);
    }
    u32 r2 = chain12(10, seed + 1);
    u32 bad = 0;
    for (u32 i = 0; i < n; i++) {
        if (p[i] != (seed ^ (i * 0x01010101u))) {
            bad++;
        }
    }
    return r + r2 + bad;
}

// Five values live across the call, in this frame's registers.
__attribute__((noinline)) u32 holder(u32 x) {
    u32 v1 = mix(x, 1), v2 = mix(x, 2), v3 = mix(x, 3), v4 = mix(x, 4);
    u32 r = alloca_after_spill(8, x);
    u32 want = ANSWER(x, 20) + ANSWER(x + 1, 10);
    u32 err = 0;
    if (r != want) {
        err |= 0x100;
    }
    if (v1 != mix(x, 1)) err |= 1;
    if (v2 != mix(x, 2)) err |= 2;
    if (v3 != mix(x, 3)) err |= 4;
    if (v4 != mix(x, 4)) err |= 8;
    return err;
}

static u32 role_chain(u32 round, u32 seed) {
    switch (round % 3) {
    case 0:
        return chain4(THREAD_DEPTH, seed);
    case 1:
        return chain8(THREAD_DEPTH, seed);
    default:
        return chain12(THREAD_DEPTH, seed);
    }
}

void worker(u32 role) {
    u32 code = 0;
    for (u32 round = 0; round < ROUNDS + 2; round++) {
        u32 seed = ((role + 1) << 28) | round;
        u32 r = role_chain(round, seed);
        if (r != ANSWER(seed, THREAD_DEPTH)) {
            code = r ? r : 1;
            say("SOS win: thread chain FAILED ");
            hex(r);
            put('\n');
            g_progress[role] = GIVE_UP;
            g_pub[0] = g_pub[1] = g_rel[0] = g_rel[1] = 1;
            break;
        }
    }
    u32 me = 0;
    (void)sos_syscall3(g_proc, PROC_THREAD_SELF, 0, 0, 0, &me);
    (void)sos_syscall1(me, THREAD_EXIT, code);
}

static u32 threads(void) {
    u32 th[2];
    for (u32 role = 0; role < 2; role++) {
        u32 top = (u32)&g_stacks[role][STACK_WORDS];
        if (sos_syscall3(g_proc, PROC_THREAD_CREATE, (u32)worker, top, role,
                         &th[role]) != 0) {
            say("SOS win: thread create FAILED\n");
            return 1;
        }
    }
    (void)sos_syscall1(th[0], THREAD_START, 0);
    (void)sos_syscall1(th[1], THREAD_START, 0);
    u32 a = 0xFFFFFFFFu, b = 0xFFFFFFFFu;
    (void)sos_syscall3(th[0], THREAD_JOIN, 0, 0, 0, &a);
    (void)sos_syscall3(th[1], THREAD_JOIN, 0, 0, 0, &b);
    say("SOS win: threads joined a=");
    hex(a);
    say(" b=");
    hex(b);
    put('\n');
    return (a != 0) | (b != 0);
}

void _start(u32 boot) {
    g_boot = boot;
    u32 ps = read_ps(), wb = read_wb(), ws = read_ws();
    say("SOS win: start ps=");
    hex(ps);
    say(" wb=");
    hex(wb);
    say(" ws=");
    hex(ws);
    put('\n');
    if (sos_syscall3(boot, SYS_PROCESS_SELF, ALL_RIGHTS, 0, 0, &g_proc) != 0) {
        say("SOS win: no process handle\n");
        shutdown(1);
    }

    u32 fail = 0;
    g_mode = MODE_NONE;
    fail |= check("chain4", chain4(DEPTH, 0x01000000u),
                  ANSWER(0x01000000u, DEPTH));
    fail |= check("chain8", chain8(DEPTH, 0x02000000u),
                  ANSWER(0x02000000u, DEPTH));
    fail |= check("chain12", chain12(DEPTH, 0x03000000u),
                  ANSWER(0x03000000u, DEPTH));
    g_mode = MODE_SYSCALL;
    fail |= check("syscall chain", chain8(30, 0x04000000u),
                  ANSWER(0x04000000u, 30));
    g_mode = MODE_NONE;
    fail |= check("alloca", holder(0x05000000u), 0);
    g_mode = MODE_SPIN;
    fail |= threads();
    say(fail ? "SOS win: FAILED\n" : "SOS win: done\n");
    shutdown(fail ? 1 : 0);
}
