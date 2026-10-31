"""CLI surface: `setup` subcommand, and NOT_SET_UP exit-code/stdout parity (Task 6 fix round 1).

Fix round 1, finding 3: list/run/save previously diverged (list exited 0, run printed
'error:' to stderr and exited 2, save exited 1). All three must now print the NOT_SET_UP
text (no 'error:' prefix) to stdout and exit 3, matching across the CLI surface; the MCP
path (isError:false) is covered separately in test_onboarding.py/test_server.py.
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from muscle_memory_mcp import cli, onboarding


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved = {k: os.environ.get(k) for k in
                       ("MUSCLE_MEMORY_DIR", "MUSCLE_MEMORY_PROJECT_DIR")}
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.root / "personal")
        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(self.root / "project")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    # -- minor (d): one CLI test for the `setup` subcommand --

    def test_setup_subcommand_creates_personal_library(self):
        code, out, err = self.run_cli(["setup", "--mode", "local"])
        self.assertEqual(code, 0)
        self.assertIn("Personal library ready at", out)
        self.assertEqual(err, "")
        self.assertEqual((self.root / "personal" / "INDEX.md").read_text(encoding="utf-8"),
                          "# Muscle memory index\n")

    # -- finding 3: NOT_SET_UP parity across list/run/save --

    def test_list_not_set_up_exits_3_no_error_prefix(self):
        code, out, err = self.run_cli(["list"])
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), onboarding.NOT_SET_UP)
        self.assertNotIn("error:", out)
        self.assertEqual(err, "")

    def test_run_not_set_up_exits_3_no_error_prefix(self):
        code, out, err = self.run_cli(["run", "whatever.py"])
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), onboarding.NOT_SET_UP)
        self.assertNotIn("error:", out)
        self.assertEqual(err, "")

    def test_save_not_set_up_exits_3_no_error_prefix(self):
        code_path = self.root / "code.py"
        test_path = self.root / "test.py"
        code_path.write_text("print('x')\n", encoding="utf-8")
        test_path.write_text("pass\n", encoding="utf-8")
        code, out, err = self.run_cli(["save", "x.py", "--code", str(code_path),
                                        "--test", str(test_path), "--area", "General",
                                        "--usage", "x \u2014 demo [pure]", "--new-area", "--no-push"])
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), onboarding.NOT_SET_UP)
        self.assertNotIn("error:", out)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
