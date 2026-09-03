# sawos — the SOS microkernel, built with the Saw toolchain.
#
# The toolchain is RESOLVED, not assumed: tools/toolchain.py tries, in order,
# SAWLANG_ROOT / SAWC / BLADE env overrides, a `sawc` on $PATH whose
# --version matches sawlang.pin, then a cached fetch of the pinned sha.
# Building the kernel needs a ROOT (SAWLANG_ROOT or the fetch) — a $PATH
# sawc alone cannot supply the source packages (sosrt, imgformat).

.PHONY: sos-test
sos-test:
	@python3 tools/sos_runner.py

# NON-GATING (design 20): the ESP32-C3 board smoke — bringup + memory config
# only, against the machine-local Espressif QEMU. Never part of sos-test,
# never CI. Three cases: boot, timer, isolation.
#
# It needs the Espressif toolchain under ~/.espressif (QEMU with the esp32c3
# machine, and esp-clang for the no-A multilib row); the target says so and
# exits 2 if either is missing, which breaks nothing else in this repo.
.PHONY: sos-smoke-esp32c3
sos-smoke-esp32c3:
	@python3 tools/sos_runner.py --board esp32c3
