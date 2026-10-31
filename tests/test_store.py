"""Task 5: save scope, clashes, replace rollback and per-folder git behaviour."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from muscle_memory_mcp import paths, store

CODE = "print('ok')\n"
GOOD_TEST = "import os, subprocess, sys\nassert subprocess.check_output([sys.executable, os.environ['MM_SCRIPT']]).decode().strip() == 'ok'\n"
BAD_TEST = "raise SystemExit(1)\n"
INDEX = "# Index\n\n## General\n\nx.py — old usage [pure]\n\ny.py — other [pure]\n"


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, encoding="utf-8",
                          check=check, timeout=20)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.personal = self.base / "personal"
        self.personal.mkdir()
        self._env = {k: os.environ.get(k) for k in ("MUSCLE_MEMORY_DIR", "MUSCLE_MEMORY_PROJECT_DIR")}
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.personal)
        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(self.project)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def setup_library(self, root, index=INDEX):
        root.mkdir(parents=True, exist_ok=True)
        (root / "scripts").mkdir(exist_ok=True)
        (root / "tests").mkdir(exist_ok=True)
        with open(root / "INDEX.md", "w", encoding="utf-8", newline="") as f:
            f.write(index)

    def init_git(self, root):
        git(root, "init", "-q")
        git(root, "config", "user.name", "Task Test")
        git(root, "config", "user.email", "task@test.invalid")
        git(root, "commit", "--allow-empty", "-qm", "initial")

    def save(self, name="new.py", code=CODE, test=GOOD_TEST, area="General", usage="new.py — new [pure]", **kw):
        return store.save(name, code, test, area, usage, **kw)

    def test_scope_project_writes_without_commit(self):
        root = self.project / ".muscle-memory"
        self.setup_library(root)
        (root / ".shared").touch()
        self.init_git(self.project)
        ok, msg = self.save(scope="project", push=False)
        self.assertTrue(ok, msg)
        self.assertIn("not committed", msg)
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=all").stdout.splitlines(),
                         ["?? .muscle-memory/.shared", "?? .muscle-memory/INDEX.md",
                          "?? .muscle-memory/scripts/new.py", "?? .muscle-memory/tests/test_new.py"])

    def test_cross_library_clash_refused(self):
        root = self.project / ".muscle-memory"
        self.setup_library(root)
        (root / "scripts" / "x.py").write_text(CODE, encoding="utf-8")
        self.setup_library(self.personal)
        with self.assertRaisesRegex(ValueError, "x.py already exists in the project library"):
            self.save("x.py")
        self.assertFalse((self.personal / "scripts" / "x.py").exists())

    def test_replace_updates_index_line_in_place(self):
        self.setup_library(self.personal)
        script = self.personal / "scripts" / "x.py"
        test = self.personal / "tests" / "test_x.py"
        script.write_text(CODE, encoding="utf-8")
        test.write_text(GOOD_TEST, encoding="utf-8")
        ok, msg = self.save("x.py", usage="x.py — updated [pure]", replace=True, push=False)
        self.assertTrue(ok, msg)
        text = (self.personal / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("x.py"), 1)
        self.assertIn("x.py — updated [pure]", text)

    def test_replace_rejects_symlink_mutation_targets_without_changes(self):
        self.setup_library(self.personal)
        root = self.personal
        script = root / "scripts" / "x.py"
        script.write_bytes(b"old script")
        victim = self.base / "victim"
        victim.write_bytes(b"outside victim")
        link = root / "tests" / "test_x.py"
        link.symlink_to(victim)
        before_index = (root / "INDEX.md").read_bytes()
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.save("x.py", replace=True, push=False)
        self.assertEqual(script.read_bytes(), b"old script")
        self.assertEqual(victim.read_bytes(), b"outside victim")
        self.assertEqual((root / "INDEX.md").read_bytes(), before_index)

    def test_rejects_symlink_metadata_runner_and_escaping_directories(self):
        self.setup_library(self.personal)
        root = self.personal
        for target in (root / "scripts" / "x.py.test.json", root / "scripts" / "x.py.runner.json"):
            outside = self.base / target.name
            outside.write_bytes(b"outside")
            target.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.save("x.py", replace=False, runner=["python"], test_name="test_x.py", test_runner=["python"], push=False)
            target.unlink()
        (root / "tests").rename(root / "tests-old")
        (root / "tests").symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.save("new.py", push=False)

    def test_replace_failing_test_restores_bytes(self):
        self.setup_library(self.personal)
        paths_to_bytes = [self.personal / "scripts" / "x.py", self.personal / "tests" / "test_x.py",
                          self.personal / "scripts" / "x.py.runner.json"]
        paths_to_bytes[0].write_bytes(b"old\r\n")
        paths_to_bytes[1].write_bytes(b"old test\r\n")
        paths_to_bytes[2].write_bytes(b'{"argv": ["python"]}\r\n')
        before = [p.read_bytes() for p in paths_to_bytes]
        ok, _ = self.save("x.py", test=BAD_TEST, replace=True, push=False)
        self.assertFalse(ok)
        self.assertEqual([p.read_bytes() for p in paths_to_bytes], before)

    def test_replace_extension_change_removes_old_test_and_writes_metadata(self):
        self.setup_library(self.personal)
        (self.personal / "INDEX.md").write_text("# Index\n\n## General\n\nx.sh — old usage [pure]\n", encoding="utf-8")
        script = self.personal / "scripts" / "x.sh"
        oldtest = self.personal / "tests" / "test_x.sh"
        script.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        oldtest.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        # Legacy .py script with shell test selection would be ambiguous; use a valid explicit override.
        (self.personal / "scripts" / "x.sh").write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        ok, msg = self.save("x.sh", code="#!/bin/bash\necho ok\n", test="assert True\n",
                            test_name="test_x.py", usage="x.sh updated [pure]", replace=True, push=False)
        self.assertTrue(ok, msg)
        self.assertFalse(oldtest.exists())
        self.assertEqual((self.personal / "scripts" / "x.sh.test.json").read_text(encoding="utf-8").strip(),
                          '{"name": "test_x.py"}')

    def test_test_name_cannot_overwrite_another_script_test(self):
        self.setup_library(self.personal)
        (self.personal / "tests" / "test_new.py").write_text("owned\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            self.save(test_name="test_new.py")
        self.assertEqual((self.personal / "tests" / "test_new.py").read_text(encoding="utf-8"), "owned\n")

    def test_replace_runner_exception_restores_exact_bytes(self):
        self.setup_library(self.personal, "# Index\n\n## General\n\nx.py — old usage [pure]\n")
        files = [self.personal / "scripts" / "x.py", self.personal / "tests" / "test_x.py",
                 self.personal / "scripts" / "x.py.runner.json", self.personal / "INDEX.md"]
        files[0].write_bytes(b"old script\\r\\n")
        files[1].write_bytes(b"old test\\r\\n")
        files[2].write_bytes(b'{"argv": ["python"]}\\r\\n')
        files[3].write_bytes(b"# custom index\\r\\n")
        before = [p.read_bytes() for p in files]
        for exc in (subprocess.TimeoutExpired("runner", 1), ValueError("runner missing")):
            with self.subTest(exc=exc), mock.patch.object(store.testing, "run_test", side_effect=exc):
                with self.assertRaises(type(exc)):
                    self.save("x.py", replace=True, runner=["python"], new_area=True)
                self.assertEqual([p.read_bytes() for p in files], before)

    def test_new_save_runner_exception_cleans_files(self):
        self.setup_library(self.personal)
        with mock.patch.object(store.testing, "run_test", side_effect=ValueError("runner missing")):
            with self.assertRaisesRegex(ValueError, "runner missing"):
                self.save(runner=["python"])
        self.assertFalse((self.personal / "scripts" / "new.py").exists())
        self.assertFalse((self.personal / "tests" / "test_new.py").exists())
        self.assertFalse((self.personal / "scripts" / "new.py.runner.json").exists())

    def test_staged_unrelated_change_not_in_library_commit(self):
        self.setup_library(self.personal)
        self.init_git(self.personal)
        unrelated = self.personal / "notes.txt"
        unrelated.write_text("user change\\n", encoding="utf-8")
        git(self.personal, "add", "notes.txt")
        ok, msg = self.save(push=False)
        self.assertTrue(ok, msg)
        changed = set(git(self.personal, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines())
        self.assertEqual(changed, {"INDEX.md", "scripts/new.py", "tests/test_new.py"})
        self.assertEqual(git(self.personal, "diff", "--cached", "--name-only").stdout.splitlines(), ["notes.txt"])

    def test_git_add_failure_never_commits(self):
        self.setup_library(self.personal)
        self.init_git(self.personal)
        before = git(self.personal, "rev-parse", "HEAD").stdout.strip()
        with mock.patch.object(store, "_run_git", side_effect=[(0, ".git", ""), (1, "", "add failed")]) as mocked:
            msg = store._save_personal_git(self.personal, "new.py", "new.py — new [pure]",
                                           ["scripts/new.py"], False, False)
        self.assertIn("add failed", msg)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(git(self.personal, "rev-parse", "HEAD").stdout.strip(), before)

    def test_personal_plain_folder_no_git(self):
        self.setup_library(self.personal)
        ok, msg = self.save(push=False)
        self.assertTrue(ok, msg)
        self.assertEqual(msg, "saved, tests pass (not under git)")

    def test_git_missing(self):
        self.setup_library(self.personal)
        with mock.patch("muscle_memory_mcp.store.shutil.which", return_value=None):
            ok, msg = self.save(push=False)
        self.assertTrue(ok, msg)
        self.assertEqual(msg, "saved, tests pass; not committed: git not found")

    def test_commit_no_remote(self):
        self.setup_library(self.personal)
        self.init_git(self.personal)
        ok, msg = self.save(push=False)
        self.assertTrue(ok, msg)
        self.assertIn("committed", msg)
        log = git(self.personal, "log", "-1", "--format=%s").stdout.strip()
        self.assertTrue(log.startswith("Add new.py:"), log)

    def test_push_failure_keeps_commit(self):
        self.setup_library(self.personal)
        self.init_git(self.personal)
        git(self.personal, "remote", "add", "origin", str(self.base / "missing-remote"))
        before = git(self.personal, "rev-parse", "HEAD").stdout.strip()
        ok, msg = self.save()
        self.assertTrue(ok, msg)
        self.assertIn("push failed", msg)
        self.assertNotEqual(git(self.personal, "rev-parse", "HEAD").stdout.strip(), before)

    def test_project_scope_needs_project_and_setup(self):
        with mock.patch.object(paths, "project_root", return_value=None):
            with self.assertRaisesRegex(ValueError, "no project here; save with scope 'personal'"):
                self.save(scope="project")
        self.init_git(self.project)
        with self.assertRaisesRegex(ValueError, "this project has no library yet: ask the user"):
            self.save(scope="project")

    def test_unmarked_project_library_in_git_warns_once(self):
        root = self.project / ".muscle-memory"
        self.setup_library(root)
        self.init_git(self.project)
        ok, msg = self.save(scope="project", push=False)
        self.assertTrue(ok, msg)
        warning = "note: this project library is now inside git but was never set up as shared or private; ask the user (mm_setup)"
        self.assertEqual(msg.count(warning), 1)
        ok, msg = self.save("another.py", usage="another.py — next [pure]", scope="project", push=False)
        self.assertTrue(ok, msg)
        self.assertEqual(msg.count(warning), 1)
        self.assertFalse((root / ".shared").exists())


if __name__ == "__main__":
    unittest.main()
