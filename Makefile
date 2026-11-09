# sawos — the SOS microkernel, built with the Saw toolchain.
#
# The toolchain is resolved, not assumed: tools/toolchain.py tries, in order,
# SAWLANG_ROOT / SAWC / BLADE env overrides, a `sawc` on $PATH (whose
# --version must match sawlang.pin; a mismatch is refused, not skipped), then
# a cached fetch of the pinned sha.
# Building the kernel needs a sawlang checkout (SAWLANG_ROOT or the fetch): a
# $PATH sawc alone cannot supply the `imgformat` package's sources.

.PHONY: sos-test
sos-test:
	@python3 tools/sos_runner.py

# Gating: the ESP32-C3 board smoke — bringup + memory config only. The
# server runs it after sos-test. Its cases are `c3_cases` in
# tools/sos_runner.py.
#
# It needs the Espressif toolchain under ~/.espressif (QEMU with the esp32c3
# machine, and esp-clang for the no-A multilib row) on the machine that runs
# the gate; the target says so and exits 2 if either is missing, which is a
# red gate, not a skip.
.PHONY: sos-smoke-esp32c3
sos-smoke-esp32c3:
	@python3 tools/sos_runner.py --board esp32c3
