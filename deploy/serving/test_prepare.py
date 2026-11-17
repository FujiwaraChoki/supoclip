"""Exercise the release budget without touching Docker or live containers."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ReleaseBudgetTests(unittest.TestCase):
    def prepare(self, live):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == "ps":
    print(os.environ["FAKE_LIVE"])
elif args[:2] == ["image", "inspect"]:
    print("sha256:test")
elif args[:2] == ["container", "inspect"]:
    sys.exit(1)
else:
    with open(os.environ["FAKE_MUTATIONS"], "a") as log:
        log.write(" ".join(args) + "\\n")
''')
            docker.chmod(0o755)
            if not shutil.which("flock"):
                # macOS test hosts lack util-linux; retain the same OS lock.
                flock = root / "flock"
                flock.write_text("#!/usr/bin/env python3\nimport fcntl, sys\nfcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)\n")
                flock.chmod(0o755)
            mutations = root / "mutations"
            result = subprocess.run(
                ["bash", str(Path(__file__).with_name("prepare.sh")),
                 "test-budget", "13009", "18009", "web:test", "api:test"],
                cwd=root, capture_output=True, text=True,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                     "FAKE_LIVE": "\n".join(live), "FAKE_MUTATIONS": str(mutations),
                     "XDG_STATE_HOME": str(root / "state")},
            )
            return result, mutations.read_text() if mutations.exists() else ""

    def test_retained_previous_pair_blocks_another_candidate_without_mutations(self):
        result, mutations = self.prepare([
            "supoclip-web-current", "supoclip-api-current",
            "supoclip-web-previous", "supoclip-api-previous",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Retire the drained previous release", result.stderr)
        self.assertEqual(mutations, "")

    def test_one_serving_pair_allows_a_candidate(self):
        result, mutations = self.prepare(["supoclip-web-current", "supoclip-api-current", "unrelated-web"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mutations.count("run --no-deps"), 2)

    def test_initial_candidate_is_allowed(self):
        result, mutations = self.prepare([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mutations.count("run --no-deps"), 2)


if __name__ == "__main__":
    unittest.main()
