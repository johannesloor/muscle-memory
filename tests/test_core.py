import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from muscle_memory_mcp import core, server

INDEX = """# Muscle memory index

## Files

list_files.py DIR — list files with size and date [read]

## Shopping

product_page.py URL — print price, stock and image from a product page [read]
"""

GOOD_CODE = "import sys\nprint(int(sys.argv[1]) * 2)\n"
GOOD_TEST = """import subprocess, sys, unittest
from pathlib import Path
class T(unittest.TestCase):
    def test_double(self):
        out = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts' / 'double.py'), '21'],
                             capture_output=True, encoding='utf-8').stdout
        self.assertEqual(out.strip(), '42')
if __name__ == '__main__':
    unittest.main()
"""
BAD_TEST = ("import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(1, 2)\n"
            "if __name__ == '__main__':\n    unittest.main()\n")


class Lib(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "INDEX.md").write_text(INDEX, encoding="utf-8")
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


class TestList(Lib):
    def test_parse_index_areas(self):
        e = core.parse_index(INDEX)
        self.assertEqual([(x["area"], x["script"]) for x in e],
                         [("Files", "list_files.py"), ("Shopping", "product_page.py")])

    def test_areas_with_counts(self):
        self.assertEqual(core.areas(), [("Files", 1), ("Shopping", 1)])
        out = core.format_areas(core.areas())
        self.assertIn("- Files (1)", out)
        self.assertIn("- Shopping (1)", out)
        self.assertNotIn("product_page.py", out)  # areas only, never scripts

    def test_area_lines_case_insensitive(self):
        name, lines = core.area_lines("shopping")
        self.assertEqual(name, "Shopping")
        self.assertEqual(lines, ["product_page.py URL — print price, stock and image from a product page [read]"])

    def test_unknown_area_lists_known(self):
        with self.assertRaises(ValueError) as cm:
            core.area_lines("Email")
        self.assertIn("Files, Shopping", str(cm.exception))

    def test_empty_library(self):
        (self.d / "INDEX.md").write_text("# Muscle memory index\n", encoding="utf-8")
        self.assertEqual(core.areas(), [])
        self.assertIn("library is empty", core.format_areas([]))


class TestRun(Lib):
    def test_run_and_traversal_guard(self):
        (self.d / "scripts" / "double.py").write_text(GOOD_CODE, encoding="utf-8")
        self.assertEqual(core.run("double.py", ["5"])[1].strip(), "10")
        (self.d / "mm.py").write_text(GOOD_CODE, encoding="utf-8")  # infrastructure outside scripts/ is not runnable
        for bad in ["../etc/passwd", "missing.py", "INDEX.md", "../mm.py"]:
            with self.assertRaises(ValueError):
                core.run(bad, [])


class TestSave(Lib):
    def test_save_indexes_and_commits(self):
        ok, msg = core.save("double.py", GOOD_CODE, GOOD_TEST, "Math",
                          "N — print N doubled [pure]", push=False, new_area=True)
        self.assertTrue(ok, msg)
        idx = (self.d / "INDEX.md").read_text(encoding="utf-8")
        self.assertTrue(idx.rstrip().endswith("## Math\n\ndouble.py N — print N doubled [pure]"))
        log = subprocess.run(["git", "-C", str(self.d), "log", "--oneline"], capture_output=True, encoding="utf-8").stdout
        self.assertIn("Add double.py", log)
        tracked = subprocess.run(["git", "-C", str(self.d), "ls-files"], capture_output=True, encoding="utf-8").stdout.split()
        self.assertEqual(sorted(tracked), ["INDEX.md", "scripts/double.py", "tests/test_double.py"])

    def test_save_into_existing_area(self):
        ok, _ = core.save("double.py", GOOD_CODE, GOOD_TEST, "Shopping", "double.py N — x [pure]", push=False)
        self.assertTrue(ok)
        lines = (self.d / "INDEX.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[lines.index("product_page.py URL — print price, stock and image from a product page [read]") + 1],
                         "double.py N — x [pure]")

    def test_failing_test_rolls_back(self):
        ok, msg = core.save("double.py", GOOD_CODE, BAD_TEST, "Math", "x [pure]", push=False, new_area=True)
        self.assertFalse(ok)
        self.assertFalse((self.d / "scripts" / "double.py").exists())
        self.assertFalse((self.d / "tests" / "test_double.py").exists())
        self.assertEqual((self.d / "INDEX.md").read_text(encoding="utf-8"), INDEX)

    def test_other_failing_test_does_not_block_save(self):
        (self.d / "tests" / "test_flaky.py").write_text(BAD_TEST, encoding="utf-8")
        ok, msg = core.save("double.py", GOOD_CODE, GOOD_TEST, "Math", "x [pure]", push=False, new_area=True)
        self.assertTrue(ok, msg)

    def test_failure_message_says_fix_and_retry(self):
        ok, msg = core.save("double.py", GOOD_CODE, BAD_TEST, "Math", "x [pure]", push=False, new_area=True)
        self.assertIn("Fix the code or the test and call save again", msg)

    def test_unknown_area_needs_new_area(self):
        with self.assertRaises(ValueError) as cm:
            core.save("double.py", GOOD_CODE, GOOD_TEST, "Webshop", "x [pure]", push=False)
        self.assertIn("Existing areas: Files, Shopping", str(cm.exception))
        self.assertFalse((self.d / "scripts" / "double.py").exists())
        self.assertEqual((self.d / "INDEX.md").read_text(encoding="utf-8"), INDEX)

    def test_area_case_insensitive_reuses_canonical(self):
        ok, msg = core.save("double.py", GOOD_CODE, GOOD_TEST, "  shopping ", "x [pure]", push=False)
        self.assertTrue(ok, msg)
        idx = (self.d / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(idx.count("## "), 2)  # no new 'shopping' section
        self.assertEqual(core.areas(), [("Files", 1), ("Shopping", 2)])

    def test_save_preserves_crlf_newlines(self):
        # Review Focus 5: save()'s _read_index_text/open(..., newline="") round trip must not
        # flip a CRLF INDEX.md to bare \n.
        crlf_index = INDEX.replace("\n", "\r\n")
        with open(self.d / "INDEX.md", "w", encoding="utf-8", newline="") as f:
            f.write(crlf_index)
        ok, msg = core.save("double.py", GOOD_CODE, GOOD_TEST, "Math",
                          "N — print N doubled [pure]", push=False, new_area=True)
        self.assertTrue(ok, msg)
        with open(self.d / "INDEX.md", "rb") as f:
            raw = f.read().decode("utf-8")
        self.assertIn("double.py N — print N doubled [pure]", raw)
        self.assertNotIn("\n", raw.replace("\r\n", ""))  # no bare \n left outside \r\n pairs

    def test_save_project_scope_also_needs_personal_setup(self):
        # Fix round 1, finding 4: core.save's missing-personal guard must fire for any scope,
        # not just scope='personal'.
        old_personal = os.environ.get("MUSCLE_MEMORY_DIR")
        old_project = os.environ.get("MUSCLE_MEMORY_PROJECT_DIR")
        missing_personal = self.d / "does-not-exist"
        os.environ["MUSCLE_MEMORY_DIR"] = str(missing_personal)
        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(self.d)
        try:
            ok, msg = core.save("double.py", GOOD_CODE, GOOD_TEST, "Math", "x [pure]",
                              scope="project", push=False, new_area=True)
        finally:
            if old_personal is None:
                os.environ.pop("MUSCLE_MEMORY_DIR", None)
            else:
                os.environ["MUSCLE_MEMORY_DIR"] = old_personal
            if old_project is None:
                os.environ.pop("MUSCLE_MEMORY_PROJECT_DIR", None)
            else:
                os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = old_project
        self.assertFalse(ok)
        from muscle_memory_mcp.onboarding import NOT_SET_UP
        self.assertEqual(msg, NOT_SET_UP)

    def test_rejects_existing_and_bad_names(self):
        (self.d / "scripts" / "double.py").write_text(GOOD_CODE, encoding="utf-8")
        for name in ["double.py", "../x.py", "Bad.py", "greet.SH", "greet.py.sh"]:
            with self.assertRaises(ValueError):
                core.save(name, GOOD_CODE, GOOD_TEST, "Math", "x", push=False, new_area=True)


SHELL_CODE = "#!/bin/bash\necho \"hello $1\"\n"
# Test file keeps the script's own extension (greet.sh -> tests/test_greet.sh) and
# runs with the script's own runner (bash), per the language-native test contract.
SHELL_TEST = "#!/bin/bash\n[ \"$(bash \"$MM_SCRIPT\" World)\" = \"hello World\" ]\n"

SWIFT_CODE = (
    "import Foundation\n"
    "let args = CommandLine.arguments\n"
    "print(\"swift-hello \\(args.count > 1 ? args[1] : \"nobody\")\")\n"
)
SWIFT_TEST = """import Foundation
let scriptPath = ProcessInfo.processInfo.environment["MM_SCRIPT"]!
let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
process.arguments = ["swift", scriptPath, "Aslan"]
let pipe = Pipe()
process.standardOutput = pipe
try process.run()
process.waitUntilExit()
let data = pipe.fileHandleForReading.readDataToEndOfFile()
let output = (String(data: data, encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
if output != "swift-hello Aslan" {
    exit(1)
}
exit(0)
"""

# ".widget" is not a real language; it stands in for "any arbitrary extension
# with an explicit runner argv", exercised via plain bash so the test needs no
# extra runtime installed. Its test file (test_greet.widget) runs with the same
# sidecar runner (bash) as the script itself.
CUSTOM_CODE = "#!/usr/bin/fake\necho \"custom $1\"\n"
CUSTOM_TEST = "#!/bin/bash\n[ \"$(bash \"$MM_SCRIPT\" Zed)\" = \"custom Zed\" ]\n"


class TestLanguages(Lib):
    """Regression tests for multi-language script support.

    Before this feature, core.resolve()/core.run() hard-required a '.py' suffix,
    so every test below that runs a non-.py script documents and locks in
    the lifted restriction (it fails against the pre-feature mm.py).
    """

    def test_run_rejects_unknown_extension_without_runner(self):
        (self.d / "scripts" / "greet.widget").write_text(CUSTOM_CODE, encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            core.run("greet.widget", ["World"])
        self.assertIn("runner", str(cm.exception))

    def test_run_shell_script_via_builtin_runner(self):
        (self.d / "scripts" / "greet.sh").write_text(SHELL_CODE, encoding="utf-8")
        code, out, err = core.run("greet.sh", ["World"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "hello World")

    @unittest.skipUnless(shutil.which("swift"), "swift not installed")
    def test_run_native_swift_script_via_builtin_runner(self):
        (self.d / "scripts" / "greet.swift").write_text(SWIFT_CODE, encoding="utf-8")
        code, out, err = core.run("greet.swift", ["Aslan"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "swift-hello Aslan")

    def test_argument_forwarding_through_run(self):
        (self.d / "scripts" / "echo_args.sh").write_text(
            '#!/bin/bash\nfor a in "$@"; do echo "[$a]"; done\n', encoding="utf-8")
        code, out, err = core.run("echo_args.sh", ["one", "two", "three"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip().splitlines(), ["[one]", "[two]", "[three]"])

    def test_existing_python_scripts_still_run_with_sys_executable(self):
        (self.d / "scripts" / "double.py").write_text(GOOD_CODE, encoding="utf-8")
        code, out, err = core.run("double.py", ["5"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "10")
        self.assertFalse((self.d / "scripts" / "double.py.runner.json").exists())

    def test_missing_runtime_gives_clear_error(self):
        (self.d / "scripts" / "greet.widget").write_text(CUSTOM_CODE, encoding="utf-8")
        (self.d / "scripts" / "greet.widget.runner.json").write_text(
            json.dumps({"argv": ["totally-missing-interpreter-xyz"]}), encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            core.run("greet.widget", [])
        self.assertIn("not found", str(cm.exception))

    def test_cannot_run_runner_metadata_file_directly(self):
        (self.d / "scripts" / "greet.widget").write_text(CUSTOM_CODE, encoding="utf-8")
        (self.d / "scripts" / "greet.widget.runner.json").write_text(
            json.dumps({"argv": ["bash"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            core.run("greet.widget.runner.json", [])

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_save_shell_script_via_builtin_runner_no_sidecar(self):
        ok, msg = core.save("greet.sh", SHELL_CODE, SHELL_TEST, "Math", "greet.sh NAME [pure]",
                          push=False, new_area=True)
        self.assertTrue(ok, msg)
        self.assertFalse((self.d / "scripts" / "greet.sh.runner.json").exists())
        code, out, err = core.run("greet.sh", ["Agent"])
        self.assertEqual(out.strip(), "hello Agent")

    @unittest.skipUnless(shutil.which("swift"), "swift not installed")
    def test_save_swift_script_via_builtin_runner_and_run(self):
        ok, msg = core.save("greet.swift", SWIFT_CODE, SWIFT_TEST, "Math", "greet.swift NAME [pure]",
                          push=False, new_area=True)
        self.assertTrue(ok, msg)
        code, out, err = core.run("greet.swift", ["Lucy"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "swift-hello Lucy")
        log = subprocess.run(["git", "-C", str(self.d), "log", "--oneline"],
                             capture_output=True, encoding="utf-8").stdout
        self.assertIn("Add greet.swift", log)

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_save_custom_runner_persists_commits_and_runs(self):
        ok, msg = core.save("greet.widget", CUSTOM_CODE, CUSTOM_TEST, "Math", "greet.widget NAME [pure]",
                          push=False, new_area=True, runner=["bash"])
        self.assertTrue(ok, msg)
        meta = self.d / "scripts" / "greet.widget.runner.json"
        self.assertTrue(meta.exists())
        self.assertEqual(json.loads(meta.read_text(encoding="utf-8"))["argv"], ["bash"])
        tracked = subprocess.run(["git", "-C", str(self.d), "ls-files"],
                                 capture_output=True, encoding="utf-8").stdout.split()
        self.assertIn("scripts/greet.widget.runner.json", tracked)
        code, out, err = core.run("greet.widget", ["Zed"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "custom Zed")

    def test_unknown_extension_without_runner_rejected_by_save(self):
        with self.assertRaises(ValueError) as cm:
            core.save("greet.widget", CUSTOM_CODE, CUSTOM_TEST, "Math", "x [pure]",
                    push=False, new_area=True)
        self.assertIn("runner", str(cm.exception))
        self.assertFalse((self.d / "scripts" / "greet.widget").exists())

    def test_invalid_runner_argv_rejected_by_save(self):
        for bad_runner in ([], [""], [123], "bash"):
            with self.assertRaises(ValueError):
                core.save("greet.widget", CUSTOM_CODE, CUSTOM_TEST, "Math", "x [pure]",
                        push=False, new_area=True, runner=bad_runner)
            self.assertFalse((self.d / "scripts" / "greet.widget").exists())
            self.assertFalse((self.d / "scripts" / "greet.widget.runner.json").exists())

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_failing_foreign_test_rolls_back_script_and_runner(self):
        bad_test = "#!/bin/bash\nexit 1\n"
        ok, msg = core.save("greet.widget", CUSTOM_CODE, bad_test, "Math", "x [pure]",
                          push=False, new_area=True, runner=["bash"])
        self.assertFalse(ok)
        self.assertFalse((self.d / "scripts" / "greet.widget").exists())
        self.assertFalse((self.d / "scripts" / "greet.widget.runner.json").exists())
        self.assertFalse((self.d / "tests" / "test_greet.widget").exists())


if __name__ == "__main__":
    unittest.main()
