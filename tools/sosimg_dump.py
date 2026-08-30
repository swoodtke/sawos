#!/usr/bin/env python3
"""Dump SawOS binary artifacts, human-readably.

  tools/sosimg_dump.py <file.sosimg>          the image header + segment table
  tools/sosimg_dump.py --regions <blob>       a raw region table (v2), e.g. from
                                              llvm-objcopy -O binary --only-section=.regions <elf> <blob>

Formats: sosimg v3 (imgformat, sawlang libs/imgformat) and the sawos region
table v2 (kernel/core/process.saw + tools/sos_runner.py — kept in step by the
static_asserts beside each).
"""
import struct
import sys

SEG_FLAGS = [(1, "R"), (2, "W"), (4, "X"), (8, "DEVICE")]
ARCHES = {1: "riscv32", 2: "arm64"}
REGION_KINDS = {0: "ram", 1: "device"}
SOSIMG_MAGIC = 0x4953_4F53   # 'S','O','S','I' LE
REGION_MAGIC = 0x4E47_5253   # 'S','R','G','N' LE


def flags_str(f):
    return "".join(name for bit, name in SEG_FLAGS if f & bit) or "-"


def dump_sosimg(path):
    blob = open(path, "rb").read()
    magic, version, seg_count, arch, prio_map, _res, entry = struct.unpack_from("<IHBBIIQ", blob, 0)
    ok = "ok" if magic == SOSIMG_MAGIC else f"BAD (want 0x{SOSIMG_MAGIC:08x})"
    print(f"magic     0x{magic:08x} ({ok})")
    print(f"version   {version}")
    print(f"arch      {arch} ({ARCHES.get(arch, '?')})")
    print(f"segments  {seg_count}")
    print(f"prio_map  0x{prio_map:08x}")
    print(f"entry     0x{entry:016x}")
    off = 24
    for i in range(seg_count):
        load, foff, flen, mlen, fl = struct.unpack_from("<QIIIB", blob, off)
        bss = f" (+{mlen - flen} bss)" if mlen > flen else ""
        print(f"  seg {i}: load 0x{load:016x}  file_off {foff:<8} file_len {flen:<8} "
              f"mem_len {mlen:<8}{bss}  [{flags_str(fl)}]")
        off += 24
    print(f"file size {len(blob)} bytes")


def dump_regions(path):
    blob = open(path, "rb").read()
    if len(blob) == 0:
        print("empty table (zero regions)")
        return
    magic, version, count, _res = struct.unpack_from("<IHBB", blob, 0)
    ok = "ok" if magic == REGION_MAGIC else f"BAD (want 0x{REGION_MAGIC:08x})"
    print(f"magic     0x{magic:08x} ({ok})")
    print(f"version   {version}")
    print(f"regions   {count}")
    off = 8
    for i in range(count):
        base, length, kind = struct.unpack_from("<QQB", blob, off)
        print(f"  row {i}: base 0x{base:016x}  len 0x{length:x} ({length})  "
              f"kind {kind} ({REGION_KINDS.get(kind, '?')})")
        off += 24


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args[0] == "--regions":
        dump_regions(args[1])
    else:
        dump_sosimg(args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
