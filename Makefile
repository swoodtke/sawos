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
