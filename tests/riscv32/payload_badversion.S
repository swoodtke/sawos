/* Fixture: a version-2 image offered to a version-3 loader.
 *
 * This is a real v2 image, byte for byte: the 16-byte header and the 20-byte
 * record the format had before addresses were widened, with correct magic, the
 * right arch tag, sane permissions and an entry inside the root region.
 * Everything about it is right except its version.
 *
 * A version mismatch is a refusal boundary, not a negotiation. There is no safe
 * reading of these bytes: a v2 record puts `file_off` at +0 and a 32-bit
 * `load_addr` at +4, where a v3 record puts its 64-bit `load_addr` (low half at
 * +0, high half at +4), and the fields after it are all four bytes out.
 * Guessing would place a segment from a number that is half of one field and
 * half of another, and then jump into it. So the loader stops at the version
 * and says so.
 */

    .equ ROOT_ENTRY,      0x80200000
    .equ PRIO_MAP,        0x01010100
    .equ SOSIMG_V2,       2                 /* deliberately not the current */
    .equ SOSIMG_ARCH,     1                 /* SosimgArch.Riscv32           */
    .equ SEG_FLAG_R,      1
    .equ SEG_FLAG_X,      4

    .section .payload, "a", @progbits
    .balign 16
_img_start:
    /* --- the v2 header: 16 bytes, a 32-bit entry --- */
    .ascii  "SOSI"
    .short  SOSIMG_V2
    .byte   1
    .byte   SOSIMG_ARCH
    .word   ROOT_ENTRY
    .word   PRIO_MAP

    /* --- the v2 segment record: 20 bytes, a 32-bit load address --- */
    .word   _seg0 - _img_start
    .word   ROOT_ENTRY
    .word   _seg0_end - _seg0
    .word   _seg0_end - _seg0
    .byte   SEG_FLAG_R | SEG_FLAG_X
    .byte   0, 0, 0

    .balign 4
_seg0:
    /* Never reached, never copied. */
1:
    j       1b
_seg0_end:
