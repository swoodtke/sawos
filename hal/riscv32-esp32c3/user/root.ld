/* ORIGIN: sibling copy of hal/riscv32/user/root.ld (the SIBLING-COPY rule,
 * ../README.md). Section layout, grouping and comments are that file's;
 * ONLY THE ADDRESSES CHANGE, to the C3's SRAM budget (see
 * ../kernel/esp32c3.ld). Keep the pair in step until the named
 * shared-core dedup pass merges them. */
/* SOS root server linker script (design 140) — riscv32, Profile A.
 *
 * Root is loaded by the kernel at a FIXED address: there is no relocation and
 * no MMU on this profile, so the addresses here and the root-region constants
 * in kernel/core/lib.saw are one decision recorded twice. The kernel
 * validates every segment against the region and rejects an image that does
 * not fit, so a mismatch is a clean diagnostic rather than a corrupted kernel.
 *
 *   0x403A_6000  ROOT_LOAD_BASE   — segments load from here
 *   0x403C_1000  stack bottom     — the kernel grants this RW separately
 *   0x403C_5000  ROOT_REGION_TOP  — initial sp
 *
 * LENGTH stops exactly at the stack bottom, so the linker cannot place data
 * where the stack will live.
 *
 * Sections are grouped so the image needs FEW loadable segments: each one costs
 * a PMP region, and the kernel's budget is small (see esp32c3.ld). Read-only data
 * shares the executable segment rather than claiming a region of its own — W^X
 * is preserved (nothing writable is executable), which is the property that
 * matters.
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
    ROOT (rwxa) : ORIGIN = 0x403A6000, LENGTH = 108K
}

SECTIONS {
    . = 0x403A6000;

    /* Code + read-only data: one R+X segment. */
    .text : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
        . = ALIGN(4);
    } > ROOT

    /* Writable data + zero-init: one R+W segment. `.bss` is NOLOAD, so its
     * bytes are not in the image — the sosimg segment's mem_len exceeds its
     * file_len and the kernel zero-fills the tail. */
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.sdata .sdata.*)
        . = ALIGN(4);
    } > ROOT

    .bss (NOLOAD) : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        _arena_start = .;
        . = . + ARENA_SIZE;
        _arena_end = .;
        . = ALIGN(16);
    } > ROOT

    /DISCARD/ : {
        *(.riscv.attributes)
        *(.comment)
        *(.eh_frame .eh_frame_hdr)
        *(.note .note.*)
    }
}
