import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from muscle_memory_mcp import cli, core, testing


def py_script(multiplier):
    return f"import sys\nprint(int(sys.argv[1]) * {multiplier})\n"


def py_test(expected):
    return f"""import os, subprocess, sys, unittest
class T(unittest.TestCase):
    def test_run(self):
        script = os.environ['MM_SCRIPT']
        out = subprocess.run([sys.executable, script, '21'], capture_output=True,
                              encoding='utf-8').stdout
        self.assertEqual(out.strip(), '{expected}')
if __name__ == '__main__':
    unittest.main()
"""


SH_SCRIPT = "#!/bin/bash\necho \"hi $1\"\n"
SH_TEST_PASS = "#!/bin/bash\n[ \"$(bash \"$MM_SCRIPT\" Jo)\" = \"hi Jo\" ]\n"
SH_TEST_FAIL = "#!/bin/bash\n[ \"$(bash \"$MM_SCRIPT\" Jo)\" = \"bye Jo\" ]\n"


class TestTestPath(unittest.TestCase):
    def test_keeps_script_extension(self):
        self.assertEqual(testing.test_path(Path("/lib"), "greet.sh"), Path("/lib/tests/test_greet.sh"))
        self.assertEqual(testing.test_path(Path("/lib"), "double.py"), Path("/lib/tests/test_double.py"))


class TestRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "scripts").mkdir()
        (self.d / "tests").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_sh_test_passes_via_env(self):
        (self.d / "scripts" / "greet.sh").write_text(SH_SCRIPT, encoding="utf-8")
        (self.d / "tests" / "test_greet.sh").write_text(SH_TEST_PASS, encoding="utf-8")
        ok, out = testing.run_test(self.d, "greet.sh")
        self.assertTrue(ok, out)

    def test_python_unittest_file_still_works(self):
        (self.d / "scripts" / "double.py").write_text(py_script(2), encoding="utf-8")
        (self.d / "tests" / "test_double.py").write_text(py_test("42"), encoding="utf-8")
        ok, out = testing.run_test(self.d, "double.py")
        self.assertTrue(ok, out)
        (self.d / "tests" / "test_double.py").write_text(py_test("43"), encoding="utf-8")
        ok, out = testing.run_test(self.d, "double.py")
        self.assertFalse(ok, out)

    def test_legacy_python_unittest_fallback_for_swift(self):
        (self.d / "scripts" / "legacy.custom").write_text("print(\"legacy\")\n", encoding="utf-8")
        (self.d / "scripts" / "legacy.custom.runner.json").write_text("{\"argv\": [\"python\"]}", encoding="utf-8")
        (self.d / "tests" / "test_legacy.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertEqual(1, 1)\n", encoding="utf-8")
        ok, out = testing.run_test(self.d, "legacy.custom")
        self.assertTrue(ok, out)

    def test_native_test_precedes_legacy_python_fallback(self):
        (self.d / "scripts" / "legacy.custom").write_text("print(\"legacy\")\n", encoding="utf-8")
        (self.d / "scripts" / "legacy.custom.runner.json").write_text("{\"argv\": [\"python\"]}", encoding="utf-8")
        (self.d / "tests" / "test_legacy.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
        (self.d / "tests" / "test_legacy.custom").write_text("assert True\n", encoding="utf-8")
        ok, out = testing.run_test(self.d, "legacy.custom")
        self.assertTrue(ok, out)

    def test_missing_native_and_legacy_tests_has_clear_error(self):
        (self.d / "scripts" / "legacy.custom").write_text("print(\"legacy\")\n", encoding="utf-8")
        (self.d / "scripts" / "legacy.custom.runner.json").write_text("{\"argv\": [\"python\"]}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no test file found.*test_legacy.custom.*test_legacy.py"):
            testing.run_test(self.d, "legacy.custom")

    def test_explicit_test_extension_uses_builtin_test_runner(self):
        (self.d / "scripts" / "greet.sh").write_text(SH_SCRIPT, encoding="utf-8")
        (self.d / "tests" / "test_greet.py").write_text("assert True\n", encoding="utf-8")
        (self.d / "scripts" / "greet.sh.test.json").write_text('{"name":"test_greet.py"}', encoding="utf-8")
        ok, out = testing.run_test(self.d, "greet.sh")
        self.assertTrue(ok, out)

    def test_python_script_uses_explicit_shell_test_runner(self):
        (self.d / "scripts" / "hello.py").write_text("print('hello')\n", encoding="utf-8")
        (self.d / "tests" / "test_hello.sh").write_text('#!/bin/bash\npython "$MM_SCRIPT" | grep -qx hello\n', encoding="utf-8")
        (self.d / "scripts" / "hello.py.test.json").write_text('{"name":"test_hello.sh","runner":["bash"]}', encoding="utf-8")
        ok, out = testing.run_test(self.d, "hello.py")
        self.assertTrue(ok, out)

    def test_metadata_rejects_traversal(self):
        (self.d / "scripts" / "greet.sh").write_text(SH_SCRIPT, encoding="utf-8")
        (self.d / "scripts" / "greet.sh.test.json").write_text('{"name":"../test_greet.py"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid test metadata"):
            testing.run_test(self.d, "greet.sh")

    def test_cross_language_python_test_uses_explicit_metadata(self):
        bash_path = shutil.which("bash")
        (self.d / "scripts" / "greet.sh").write_text(SH_SCRIPT, encoding="utf-8")
        (self.d / "tests" / "test_greet.py").write_text(
            "import os, subprocess, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_run(self): self.assertEqual(subprocess.check_output([{bash_path!r}, os.environ['MM_SCRIPT'], 'Jo'], encoding='utf-8').strip(), 'hi Jo')\n"
            "if __name__ == '__main__': unittest.main()\n",
            encoding="utf-8")
        (self.d / "scripts" / "greet.sh.test.json").write_text('{"name":"test_greet.py","runner":["python"]}', encoding="utf-8")
        ok, out = testing.run_test(self.d, "greet.sh")
        self.assertTrue(ok, out)

    def test_paths_with_spaces(self):
        d = self.d / "lib with space"
        (d / "scripts").mkdir(parents=True)
        (d / "tests").mkdir()
        (d / "scripts" / "double.py").write_text(py_script(2), encoding="utf-8")
        (d / "tests" / "test_double.py").write_text(py_test("42"), encoding="utf-8")
        ok, out = testing.run_test(d, "double.py")
        self.assertTrue(ok, out)


class Lib(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "INDEX.md").write_text("# Muscle memory index\n", encoding="utf-8")
        (self.d / "scripts").mkdir()
        (self.d / "tests").mkdir()
        subprocess.run(["git", "init", "-q", str(self.d)], check=True)
        subprocess.run(["git", "-C", str(self.d), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.d), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(self.d), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.old = os.environ.get("MUSCLE_MEMORY_DIR")
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.d)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("MUSCLE_MEMORY_DIR", None)
        else:
            os.environ["MUSCLE_MEMORY_DIR"] = self.old
        self.tmp.cleanup()


class TestFailingNativeTestRollsBack(Lib):
    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_failing_native_test_rolls_back(self):
        ok, msg = core.save("greet.sh", SH_SCRIPT, SH_TEST_FAIL, "Math", "greet.sh NAME [pure]",
                             push=False, new_area=True)
        self.assertFalse(ok)
        self.assertFalse((self.d / "scripts" / "greet.sh").exists())
        self.assertFalse((self.d / "tests" / "test_greet.sh").exists())
        self.assertTrue(msg.startswith("test failed; nothing saved"), msg)


class TestCliTestAll(Lib):
    def test_cli_test_all(self):
        ok, msg = core.save("double.py", py_script(2), py_test("42"), "Math", "double.py N [pure]",
                             push=False, new_area=True)
        self.assertTrue(ok, msg)
        ok, msg = core.save("triple.py", py_script(3), py_test("63"), "Math", "triple.py N [pure]",
                             push=False)
        self.assertTrue(ok, msg)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["test"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "2 passed\n")

        (self.d / "tests" / "test_triple.py").write_text(py_test("999"), encoding="utf-8")
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            code2 = cli.main(["test"])
        self.assertEqual(code2, 1)
        self.assertIn("triple.py", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
