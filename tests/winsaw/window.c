// The window probe of the windowed Saw cases (ESP32-S3): WINDOWSTART as the
// probe's own frame sees it.
//
// The `rsr` is encoded by hand (`0x03 SR T<<4`, low byte first; WINDOWSTART
// is special register 73, and T is a2), because the assembler refuses the
// register's name in a unit built call0, and this unit is built with whatever
// features the package that links it states. A call0 image reads 1 here, the
// start state's one bit, since call0 code never executes `entry`. A windowed
// image reads at least two bits: this function's `entry` set its own, and the
// caller's frame, one call behind, is live (an overflow spills frames ahead
// of the window, never the caller behind it).

typedef unsigned int u32;

u32 sos_window_start(void) {
    register u32 v __asm__("a2");
    __asm__ volatile(".byte 0x20, 0x49, 0x03" : "=a"(v));
    return v;
}
