"""Packaging checks (controller Ruling R11): the sdist must never ship private working files.

Builds the real sdist/wheel with `uv build` into a throwaway --out-dir and inspects the actual
archive contents; skipped entirely if `uv` isn't on PATH. The sdist must contain no member path
under '.superpowers' or 'docs/superpowers' (hatch's own sdist allow-list in pyproject.toml is
what's responsible for this, not .gitignore — sdists ignore .gitignore). The wheel must contain
only the muscle_memory_mcp/ package and its dist-info.
"""
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UV = shutil.which("uv")


@unittest.skipUnless(UV, "uv not found on PATH")
class SdistAllowlistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dist = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, *extra_args):
        subprocess.run([UV, "build", *extra_args, "--out-dir", str(self.dist)],
                       cwd=str(REPO_ROOT), check=True, capture_output=True, encoding="utf-8", timeout=180)

    def test_sdist_excludes_private_working_files(self):
        self._build("--sdist")
        sdists = list(self.dist.glob("*.tar.gz"))
        self.assertEqual(len(sdists), 1, sdists)
        with tarfile.open(sdists[0]) as tar:
            names = tar.getnames()
        self.assertTrue(names, "sdist is empty")
        for n in names:
            self.assertNotIn(".superpowers", n, f"private working file leaked into sdist: {n}")
            self.assertNotIn("docs/superpowers", n, f"private working file leaked into sdist: {n}")

    def test_wheel_contains_only_package_and_dist_info(self):
        self._build("--wheel")
        wheels = list(self.dist.glob("*.whl"))
        self.assertEqual(len(wheels), 1, wheels)
        with zipfile.ZipFile(wheels[0]) as z:
            names = z.namelist()
        self.assertTrue(names, "wheel is empty")
        for n in names:
            self.assertTrue(n.startswith("muscle_memory_mcp/") or ".dist-info/" in n,
                             f"unexpected wheel member: {n}")


if __name__ == "__main__":
    unittest.main()
