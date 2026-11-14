"""Capture the legacy golden pins (tests/fixtures/legacy_pins/expected/).

The pins freeze today's legacy behaviour (ASS output, ffmpeg argv, call kwargs, sidecar
keys, API JSON, SQL text, AI-edit prompt, editor document dump) so dynamic edits can prove
that "off is legacy". Re-capturing replaces that proof, so the script refuses to touch
existing pins unless ``--accept`` is given. Run it only for an intended legacy change.

    cd backend && .venv/bin/python scripts/fx/capture_legacy_pins.py [--accept]

It runs ``tests/unit/test_dynamic_edits_legacy_pins.py`` with ``LEGACY_PINS_WRITE=1`` (each
test writes the value it computes), then once more normally to check the result.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
EXPECTED = BACKEND / "tests" / "fixtures" / "legacy_pins" / "expected"
TEST_MODULE = "tests/unit/test_dynamic_edits_legacy_pins.py"
PIN_SUFFIXES = (".json", ".txt")


def _pin_files() -> list[Path]:
    if not EXPECTED.is_dir():
        return []
    return sorted(path for path in EXPECTED.iterdir() if path.is_file() and path.suffix in PIN_SUFFIXES)


def _pytest(write: bool) -> int:
    env = {key: value for key, value in os.environ.items() if key != "LEGACY_PINS_WRITE"}
    if write:
        env["LEGACY_PINS_WRITE"] = "1"
    command = [sys.executable, "-m", "pytest", "--no-cov", "-q", "-p", "no:cacheprovider", TEST_MODULE]
    return subprocess.run(command, cwd=BACKEND, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--accept", action="store_true",
        help="replace the existing pins (only for an intended change to legacy behaviour)",
    )
    args = parser.parse_args(argv)

    existing = _pin_files()
    if existing and not args.accept:
        print(
            f"Refusing to overwrite {len(existing)} legacy pin files in {EXPECTED.relative_to(BACKEND)}.\n"
            "The pins are the proof that dynamic edits leave the legacy path unchanged. If a legacy\n"
            "change is intended, re-run with --accept and review the diff before committing.",
            file=sys.stderr,
        )
        return 2

    # Start from an empty set so a pin that no longer exists does not linger.
    for path in existing:
        path.unlink()
    EXPECTED.mkdir(parents=True, exist_ok=True)

    if _pytest(write=True) != 0:
        print("Capturing the pins failed; see the test output above.", file=sys.stderr)
        return 1
    if _pytest(write=False) != 0:
        print("The captured pins do not reproduce; a pinned value is not deterministic.", file=sys.stderr)
        return 1
    print(f"Captured {len(_pin_files())} pin files in {EXPECTED.relative_to(BACKEND)}. Review the diff.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
