import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from muscle_memory_mcp import onboarding, server, store, trust

CODE = "print('ok')\n"
GOOD_TEST = ("import os, subprocess, sys\n"
             "assert subprocess.check_output([sys.executable, os.environ['MM_SCRIPT']]).decode().strip() "
             "== 'ok'\n")


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._trust_patch = mock.patch.object(trust, "_state_path", return_value=self.root / "trust.json")
        self._trust_patch.start()
        self.old_personal = os.environ.get("MUSCLE_MEMORY_DIR")
        self.old_project = os.environ.get("MUSCLE_MEMORY_PROJECT_DIR")
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.root / "personal")
        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(self.root / "project")

    def tearDown(self):
        for key, value in (("MUSCLE_MEMORY_DIR", self.old_personal),
                           ("MUSCLE_MEMORY_PROJECT_DIR", self.old_project)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._trust_patch.stop()
        self.tmp.cleanup()

    def test_missing_personal_returns_not_set_up(self):
        text, is_error = server.call("mm_list", {})
        self.assertFalse(is_error)
        self.assertEqual(text, onboarding.NOT_SET_UP)

    def test_setup_local_then_list_empty(self):
        onboarding.setup_personal("local")
        text, is_error = server.call("mm_list", {})
        self.assertFalse(is_error)
        self.assertIn("library is empty", text)
        self.assertEqual((self.root / "personal" / "INDEX.md").read_text(encoding="utf-8"),
                          "# Muscle memory index\n")

    def test_setup_existing_keeps_files(self):
        path = self.root / "existing"
        path.mkdir()
        index = "# custom\n\n## Area\n\nthing.py — keep [pure]\n"
        (path / "INDEX.md").write_text(index, encoding="utf-8")
        onboarding.setup_personal("existing", str(path))
        self.assertEqual((path / "INDEX.md").read_text(encoding="utf-8"), index)
        self.assertTrue((path / "scripts").is_dir())
        self.assertTrue((path / "tests").is_dir())

    def test_setup_clone_from_local_bare_repo(self):
        bare = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        target = self.root / "cloned"
        onboarding.setup_personal("clone", str(target), remote=str(bare))
        self.assertTrue((target / ".git").is_dir())
        self.assertTrue((target / "INDEX.md").is_file())

    def test_nondefault_path_mentions_env_line(self):
        target = self.root / "elsewhere"
        text = onboarding.setup_personal("local", str(target))
        self.assertIn(f"Add MUSCLE_MEMORY_DIR={target.resolve()} to this server's env in your MCP config, then reconnect.", text)

    def test_project_private_adds_exclude(self):
        project = self.root / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Task Test"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "task@test.invalid"], check=True)
        text = onboarding.setup_project(False)
        exclude = subprocess.check_output(["git", "-C", str(project), "rev-parse", "--git-path", "info/exclude"],
                                           encoding="utf-8").strip()
        exclude = Path(exclude)
        if not exclude.is_absolute():
            exclude = project / exclude
        self.assertIn(".muscle-memory/", exclude.read_text(encoding="utf-8"))
        self.assertIn("private", text)

        trust.approve(project / ".muscle-memory")
        ok, msg = store.save("new.py", CODE, GOOD_TEST, "General", "new.py \u2014 new [pure]",
                              scope="project", new_area=True, push=False)
        self.assertTrue(ok, msg)
        status = subprocess.run(["git", "-C", str(project), "status", "--porcelain"],
                                 capture_output=True, encoding="utf-8", check=True).stdout
        self.assertEqual(status, "")

    def test_setup_project_private_in_git_worktree(self):
        main_repo = self.root / "main"
        main_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(main_repo)], check=True)
        subprocess.run(["git", "-C", str(main_repo), "config", "user.name", "Task Test"], check=True)
        subprocess.run(["git", "-C", str(main_repo), "config", "user.email", "task@test.invalid"], check=True)
        (main_repo / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(main_repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(main_repo), "commit", "-q", "-m", "initial"], check=True)

        worktree = self.root / "worktree"
        subprocess.run(["git", "-C", str(main_repo), "worktree", "add", "-b", "feature", str(worktree)],
                        check=True, capture_output=True, encoding="utf-8")

        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(worktree)
        text = onboarding.setup_project(False)
        self.assertIn("private", text)

        exclude = subprocess.check_output(
            ["git", "-C", str(worktree), "rev-parse", "--git-path", "info/exclude"], encoding="utf-8").strip()
        exclude = Path(exclude)
        if not exclude.is_absolute():
            exclude = worktree / exclude
        self.assertIn(".muscle-memory/", exclude.read_text(encoding="utf-8"))

        trust.approve(worktree / ".muscle-memory")
        ok, msg = store.save("new.py", CODE, GOOD_TEST, "General", "new.py \u2014 new [pure]",
                              scope="project", new_area=True, push=False)
        self.assertTrue(ok, msg)
        status = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                                 capture_output=True, encoding="utf-8", check=True).stdout
        self.assertEqual(status, "")

    def test_project_shared_marker(self):
        project = self.root / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        onboarding.setup_project(True)
        self.assertTrue((project / ".muscle-memory" / ".shared").is_file())

    def test_project_no_git(self):
        text = onboarding.setup_project(False)
        self.assertIn("not under git", text)
        self.assertFalse((self.root / "project" / ".muscle-memory" / ".shared").exists())

    def test_project_shared_none_under_git_refuses_and_creates_nothing(self):
        # Fix round 1, finding 2: shared=None under git must not silently leave the library
        # unprotected; it must refuse and create nothing until the agent asks the user.
        project = self.root / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        with self.assertRaises(ValueError) as cm:
            onboarding.setup_project(None)
        msg = str(cm.exception)
        self.assertIn("shared", msg)
        self.assertIn("mm_setup(target='project'", msg)
        self.assertFalse((project / ".muscle-memory").exists())

    def test_setup_clone_into_nonempty_path_refused_file_untouched(self):
        # Fix round 1, minor b: clone into an existing non-empty path is refused, and whatever
        # was already there is left alone.
        bare = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        target = self.root / "occupied"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("do not touch\n", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            onboarding.setup_personal("clone", str(target), remote=str(bare))
        self.assertIn("not empty", str(cm.exception))
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not touch\n")
        self.assertFalse((target / ".git").exists())

    def test_setup_bad_mode_raises(self):
        # Fix round 1, minor b: a bad mode is rejected with a clear error, not mis-routed.
        with self.assertRaises(ValueError) as cm:
            onboarding.setup_personal("bogus")
        self.assertIn("mode must be", str(cm.exception))

    def test_setup_clone_without_git_reports_plainly(self):
        # Fix round 1, minor c: git missing from PATH reports plainly, not a raw FileNotFoundError.
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError) as cm:
                onboarding.setup_personal("clone", str(self.root / "cloned"), remote="https://example.invalid/repo.git")
        self.assertEqual(str(cm.exception), "git not found")

    def test_setup_local_git_init_without_git_reports_plainly(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError) as cm:
                onboarding.setup_personal("local", git=True)
        self.assertEqual(str(cm.exception), "git not found")

    def test_setup_project_without_git_reports_plainly(self):
        with mock.patch("shutil.which", return_value=None):
            text = onboarding.setup_project(False)
        self.assertIn("git not found", text)


if __name__ == "__main__":
    unittest.main()
