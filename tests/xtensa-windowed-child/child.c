// A windowed C child for `death-notify`'s root, on the ESP32-S3. One source
// serves eight packages: each package's linker script sets
// `WINDOWED_CHILD_ACT`, and the act is what the child does with its life.
//
//   1  exit: a call8 chain 30 frames deep, then `exit(5)` from its bottom
//   2  return: wrap the register file, so the pane the thread started in is
//      spilled, then return from the entry
//   3  spill4, 4 spill8, 5 spill12: a window spill below 0x10000010, through
//      the overflow handler of that call size
//   6  fill4, 7 fill8, 8 fill12: a window fill from below 0x10000010,
//      through the underflow handler of that call size
//
// Each act but exit ends the process with a fault the kernel reports; a line
// after the act says the fault did not come.

typedef unsigned int u32;

u32 sos_syscall1(u32 handle, u32 op, u32 arg0);
u32 sos_syscall3(u32 handle, u32 op, u32 arg0, u32 arg1, u32 arg2,
                 u32 *value_out);
void child_chain(u32 depth);
void wrap_chain(u32 depth);
void spill_via4(u32 bad);
void spill_via8(u32 bad);
void spill_via12(u32 bad);
void fill_via4(u32 bad);
void fill_via8(u32 bad);
void fill_via12(u32 bad);

// An absolute symbol the package's linker script defines: its address is the
// act. The acts start at 1, because the compiler takes an array's address
// for non-zero.
extern char WINDOWED_CHILD_ACT[];

#define SYS_DEBUG_PRINT  0u
#define SYS_PROCESS_SELF 2u
#define PROC_EXIT        2u
#define ALL_RIGHTS       0xFFFFFFFFu

#define EXIT_CODE   5u
#define EXIT_DEPTH  30u
#define WRAP_DEPTH  20u

// Unmapped: a store or a load there faults (ABI.md §3c probes the load).
#define BAD_STACK 0x10000010u

static u32 g_boot;

static void say(const char *s) {
    while (*s) {
        (void)sos_syscall1(g_boot, SYS_DEBUG_PRINT, (unsigned char)*s++);
    }
}

// The bottom of `child_chain`: the exit, from depth.
void child_bottom(void) {
    say("SOS winchild: exiting from depth 30\n");
    u32 proc = 0;
    if (sos_syscall3(g_boot, SYS_PROCESS_SELF, ALL_RIGHTS, 0, 0, &proc) != 0) {
        say("SOS winchild: no process handle\n");
        return;
    }
    (void)sos_syscall1(proc, PROC_EXIT, EXIT_CODE);
}

void _start(u32 boot) {
    g_boot = boot;
    switch ((u32)WINDOWED_CHILD_ACT) {
    case 1:
        child_chain(EXIT_DEPTH);
        say("SOS winchild: the exit returned\n");
        return;
    case 2:
        wrap_chain(WRAP_DEPTH);
        say("SOS winchild: returning from the entry\n");
        return;
    case 3:
        say("SOS winchild: spilling below 0x10000010 through call4\n");
        spill_via4(BAD_STACK);
        break;
    case 4:
        say("SOS winchild: spilling below 0x10000010 through call8\n");
        spill_via8(BAD_STACK);
        break;
    case 5:
        say("SOS winchild: spilling below 0x10000010 through call12\n");
        spill_via12(BAD_STACK);
        break;
    case 6:
        say("SOS winchild: filling from below 0x10000010 through call4\n");
        fill_via4(BAD_STACK);
        break;
    case 7:
        say("SOS winchild: filling from below 0x10000010 through call8\n");
        fill_via8(BAD_STACK);
        break;
    case 8:
        say("SOS winchild: filling from below 0x10000010 through call12\n");
        fill_via12(BAD_STACK);
        break;
    default:
        say("SOS winchild: no such act\n");
        return;
    }
    say("SOS winchild: the access did not fault\n");
}
