"""Project trust gates execution and opt-in personal pushes."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from muscle_memory_mcp import core, runners, store, testing, trust

CODE = "print('safe')\n"
TEST = ("import os, subprocess, sys\n"
        "assert subprocess.check_output([sys.executable, os.environ['MM_SCRIPT']]).decode().strip() == 'safe'\n")


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.lib = self.project / ".muscle-memory"
        self.personal = self.root / "personal"
        self.project.mkdir()
        self.personal.mkdir()
        for lib in (self.lib, self.personal):
            (lib / "scripts").mkdir(parents=True)
            (lib / "tests").mkdir()
            (lib / "INDEX.md").write_text("# Index\n", encoding="utf-8")
        self.old_env = {k: os.environ.get(k) for k in ("MUSCLE_MEMORY_DIR", "MUSCLE_MEMORY_PROJECT_DIR")}
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.personal)
        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(self.project)
        self.state = self.root / "trust.json"
        self.trust_patch = mock.patch.object(trust, "_state_path", return_value=self.state)
        self.trust_patch.start()

    def tearDown(self):
        self.trust_patch.stop()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_project_run_and_test_deny_before_subprocess(self):
        (self.lib / "scripts/x.py").write_text(CODE, encoding="utf-8")
        (self.lib / "tests/test_x.py").write_text(TEST, encoding="utf-8")
        with mock.patch.object(runners, "run_process") as launch:
            with self.assertRaisesRegex(ValueError, "not trusted"):
                core.run("x.py", [])
            with self.assertRaisesRegex(ValueError, "not trusted"):
                testing.run_test(self.lib, "x.py")
        launch.assert_not_called()

    def test_untrusted_project_save_writes_and_runs_nothing(self):
        args = ("new.py", CODE, TEST, "General", "new.py — safe [pure]")
        with mock.patch.object(runners, "run_process", side_effect=AssertionError("untrusted test launched")) as launch:
            with self.assertRaisesRegex(ValueError, "trust --project"):
                store.save(*args, scope="project", new_area=True)
        launch.assert_not_called()
        self.assertFalse((self.lib / "scripts/new.py").exists())
        self.assertFalse((self.lib / "tests/test_new.py").exists())
        self.assertEqual((self.lib / "INDEX.md").read_text(encoding="utf-8"), "# Index\n")

    def test_trusted_project_save_runs_test_and_saves(self):
        trust.approve(self.lib)
        ok, msg = store.save("new.py", CODE, TEST, "General", "new.py — safe [pure]",
                             scope="project", new_area=True)
        self.assertTrue(ok, msg)
        self.assertIn("new.py", (self.lib / "INDEX.md").read_text(encoding="utf-8"))

    def test_approval_covers_future_project_changes(self):
        (self.lib / "scripts/x.py").write_text(CODE, encoding="utf-8")
        (self.lib / "tests/test_x.py").write_text(TEST, encoding="utf-8")
        trust.approve(self.lib)
        (self.lib / "scripts/x.py").write_text("print('changed')\n", encoding="utf-8")
        (self.lib / "tests/test_x.py").write_text("pass\n", encoding="utf-8")
        (self.lib / "scripts/x.py.runner.json").write_text('{"argv":["python3"]}', encoding="utf-8")
        self.assertTrue(trust.approved(self.lib))
        trust.require(self.lib)

    def test_approval_is_bound_to_the_resolved_library_path(self):
        trust.approve(self.lib)
        other = self.root / "other" / ".muscle-memory"
        (other / "scripts").mkdir(parents=True)
        self.assertFalse(trust.approved(other))

    def test_project_local_forged_trust_record_is_ignored_and_state_is_external(self):
        (self.lib / "scripts/x.py").write_text(CODE, encoding="utf-8")
        (self.lib / "tests/test_x.py").write_text(TEST, encoding="utf-8")
        forged = self.lib / "trusted-projects.json"
        forged.write_text("{}", encoding="utf-8")
        (self.lib / ".trusted").write_text("approved", encoding="utf-8")
        self.assertFalse(trust.approved(self.lib))
        with self.assertRaisesRegex(ValueError, "not trusted"):
            trust.require(self.lib)
        trust.approve(self.lib)
        self.assertTrue(self.state.is_file())
        self.assertNotEqual(self.state, forged)

    def _git_personal_with_remote(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.personal), *args], check=True, capture_output=True)
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("remote", "add", "origin", str(self.remote))
        (self.personal / "INDEX.md").write_text("# Index\n\n## General\n\nseed.py — seed [pure]\n", encoding="utf-8")
        (self.personal / "scripts/seed.py").write_text("pass\n", encoding="utf-8")
        git("add", "scripts/seed.py", "INDEX.md")
        git("commit", "-qm", "seed")
        self.branch = subprocess.check_output(["git", "-C", str(self.personal), "branch", "--show-current"],
                                              encoding="utf-8").strip()
        git("push", "-q", "-u", "origin", self.branch)
        self.version = 0

    def _save_next(self, **kw):
        self.version += 1
        code = f"print('safe{self.version}')\n"
        test = TEST.replace("'safe'", f"'safe{self.version}'")
        ok, msg = store.save("local.py", code, test, "General", "local.py — local [pure]",
                             replace=self.version > 1, **kw)
        self.assertTrue(ok, msg)
        return msg

    def _remote_is_current(self):
        remote_head = subprocess.check_output(["git", "--git-dir", str(self.remote), "rev-parse", self.branch],
                                              encoding="utf-8").strip()
        return remote_head == git_head(self.personal)

    def test_personal_save_does_not_push_without_setup_choice(self):
        self._git_personal_with_remote()
        self._save_next()
        self.assertFalse(self._remote_is_current())

    def test_setup_push_choice_controls_saves_until_changed(self):
        from muscle_memory_mcp import onboarding
        self._git_personal_with_remote()
        self.assertIn("push", onboarding.setup_personal("existing", str(self.personal), push=True).lower())
        self.assertIn("pushed", self._save_next())
        self.assertTrue(self._remote_is_current())
        onboarding.setup_personal("existing", str(self.personal), push=False)
        self._save_next()
        self.assertFalse(self._remote_is_current())

    def test_setup_push_choice_is_local_git_config_not_library_content(self):
        from muscle_memory_mcp import onboarding
        self._git_personal_with_remote()
        onboarding.setup_personal("existing", str(self.personal), push=True)
        value = subprocess.check_output(["git", "-C", str(self.personal), "config", "--local", "--get",
                                         "muscle-memory.push"], encoding="utf-8").strip()
        self.assertEqual(value, "true")
        status = subprocess.check_output(["git", "-C", str(self.personal), "status", "--porcelain"], encoding="utf-8")
        self.assertEqual(status, "")

    def test_explicit_cli_no_push_overrides_setup_choice(self):
        from muscle_memory_mcp import onboarding
        self._git_personal_with_remote()
        onboarding.setup_personal("existing", str(self.personal), push=True)
        self._save_next(push=False)
        self.assertFalse(self._remote_is_current())

    def test_mcp_setup_rejects_non_boolean_push(self):
        from muscle_memory_mcp import server
        for value in ("false", 1, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "push must be a boolean"):
                server.call("mm_setup", {"mode": "existing", "path": str(self.personal), "push": value})


def git_head(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], encoding="utf-8").strip()


if __name__ == "__main__":
    unittest.main()
