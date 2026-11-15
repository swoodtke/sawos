// The Alloca exception on the ESP32-S3: `movsp` in a frame whose caller has
// been spilled, under each size the caller can have called it with.
//
//   SOS alloca: first frame p0=0x51ce0001 p11=0x51ce000b
//   SOS alloca: under call4 0x00000400
//   SOS alloca: under call8 0x00000800
//   SOS alloca: under call12 0x00001200
//   SOS alloca: done
//
// The first line is `alloca` in this thread's first frame, `_start`, after
// the pane the thread started in was spilled: the caller is the start
// state's, entered as if by call4. Each `under` line is `holdN` (`alloca.S`)
// keeping values in every register its frame owns across a callN to a frame
// that moves its stack pointer after spilling it, and answers its seed when
// each came back, or 0x80000000 with the number of the first that did not.
// Every value written before a spill is read again after a second wrap of the
// register file.

typedef unsigned int u32;

u32 sos_syscall1(u32 handle, u32 op, u32 arg0);
void wrap_chain(u32 depth);
u32 hold4(u32 seed);
u32 hold8(u32 seed);
u32 hold12(u32 seed);

#define SYS_DEBUG_PRINT 0u
#define SYS_SHUTDOWN    1u

// Deep enough in call8 frames to wrap the register file.
#define WRAP_DEPTH 20u

static u32 g_boot;

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

static u32 under(const char *what, u32 got, u32 seed) {
    say("SOS alloca: under ");
    say(what);
    put(' ');
    hex(got);
    put('\n');
    return got != seed;
}

void _start(u32 boot) {
    g_boot = boot;
    wrap_chain(WRAP_DEPTH);
    volatile u32 n = 48;
    volatile u32 *p = (volatile u32 *)__builtin_alloca(n);
    p[0] = 0x51CE0001u;
    p[11] = 0x51CE000Bu;
    wrap_chain(WRAP_DEPTH);
    say("SOS alloca: first frame p0=");
    hex(p[0]);
    say(" p11=");
    hex(p[11]);
    put('\n');
    u32 fail = (p[0] != 0x51CE0001u) | (p[11] != 0x51CE000Bu);
    fail |= under("call4", hold4(0x0400u), 0x0400u);
    fail |= under("call8", hold8(0x0800u), 0x0800u);
    fail |= under("call12", hold12(0x1200u), 0x1200u);
    say(fail ? "SOS alloca: FAILED\n" : "SOS alloca: done\n");
    (void)sos_syscall1(g_boot, SYS_SHUTDOWN, fail);
    for (;;) {
    }
}
