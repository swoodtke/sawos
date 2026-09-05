/* SOS SECOND-CHILD process linker script (sawos design 21, M4 unit 5) —
 * riscv32, Profile A.
 *
 * **`child.ld` WITH ONE NUMBER CHANGED, AND ITS OWN FILE'S PREDICTION KEPT.**
 * That file says, of the base it picks: *"A second child would take the next
 * 256 KiB, and the config — not this file — is what would say so."* Unit 5 is
 * the first case in this tree with TWO children at once — driver-as-service
 * needs a driver child and a client child alive together — so here is the next
 * 256 KiB, said by the config.
 *
 *   0x8028_0000  CHILD2_LOAD_BASE — one region above the first child's
 *   0x802B_C000  stack bottom     — the kernel grants this RW separately
 *   0x802C_0000  CHILD2_REGION_TOP — initial sp (256 KiB region, 16 KiB stack)
 *
 * **THE BASE IS `tools/sos_runner.py`'s ARITHMETIC, NOT A SECOND OPINION**:
 * `child_region_base + 1 * CHILD_REGION_LEN`. A package's manifest picks a
 * SCRIPT and the runner's `_region_rows` assigns destinations BY INDEX, so a
 * package linked with this file is a package the case must list SECOND. That
 * coupling is the same one `child.ld` has always had with index 0; it is now
 * written down at both ends.
 *
 * **IT COLLIDES WITH THE RAM POOL, DELIBERATELY AND HARMLESSLY.** The pool a
 * `"pool": True` case publishes starts at 0x8028_0000 — the same address —
 * because the pool was chosen as "one region above the one child region above
 * root". A case cannot have two children AND a pool; `_region_rows` refuses
 * that combination rather than emitting a table whose rows overlap. Widening
 * the layout is a runner decision the day a case wants both.
 *
 * Everything else — the section grouping, the 240 KiB length that stops at the
 * stack bottom, the discarded notes — is `child.ld`'s, for `child.ld`'s
 * reasons.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

/* THE ARENA (sawos design 38) — `sosrt`'s bump-allocator region, which every
 * freestanding Saw image needs because the compiler's own panic path can
 * allocate.
 *
 * `ARENA_SIZE` IS THE KNOB, and 64 KiB is what a package that says nothing
 * gets. A package that wants another size writes a script of its own that
 * assigns the symbol and then INCLUDEs this file (`tests/arena-small/` is the
 * worked example); `PROVIDE` is what makes that work, since it defines the
 * symbol only when nothing else has, so the override wins with no conditional
 * anywhere. `rt/common_c/support.c` reads `_arena_start` and `_arena_end` back
 * — Saw cannot name a linker symbol — and `rt/common/src/lib.saw` allocates
 * between them; see that file for why the arena exists at all.
 *
 * It is `.bss`-resident, so it costs the IMAGE nothing and the process's
 * REGION everything: design 20 finding 4 measured 65,536 bytes of arena in a
 * 67,600-byte child, on a part with 400 KiB of SRAM.
 */
PROVIDE(ARENA_SIZE = 64K);

MEMORY {
    CHILD2 (rwxa) : ORIGIN = 0x80280000, LENGTH = 240K
}

SECTIONS {
    . = 0x80280000;

    /* Code + read-only data: one R+X segment. */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > CHILD2

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > CHILD2

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > CHILD2

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
