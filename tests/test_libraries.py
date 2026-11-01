"""Personal + project libraries: merged listing, labels, run precedence (Task 4)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from muscle_memory_mcp import cli, core, index, paths

PY_PRINT = "print({!r})\n"


def make_lib(root: Path, index_text: str):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    # Path.write_text() only gained newline= in Python 3.13; this project supports >=3.10.
    with open(root / "INDEX.md", "w", encoding="utf-8", newline="") as f:
        f.write(index_text)
    return root


class LibrariesTestCase(unittest.TestCase):
    """Fakes HOME (and USERPROFILE) via temp dirs; never touches the real home (ruling c)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.personal = self.home / ".muscle-memory"
        self.project_dir = self.base / "project"
        self.project_dir.mkdir()
        self.project = self.project_dir / ".muscle-memory"

        self._saved_env = {k: os.environ.get(k) for k in
                            ("MUSCLE_MEMORY_DIR", "MUSCLE_MEMORY_PROJECT_DIR", "HOME", "USERPROFILE")}
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.personal)
        os.environ.pop("MUSCLE_MEMORY_PROJECT_DIR", None)
        os.environ["HOME"] = str(self.home)
        os.environ["USERPROFILE"] = str(self.home)
        self._home_patch = mock.patch.object(Path, "home", return_value=self.home)
        self._home_patch.start()
        from muscle_memory_mcp import trust
        self._trust_patch = mock.patch.object(trust, "_state_path", return_value=self.base / "trust.json")
        self._trust_patch.start()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        self._home_patch.stop()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def set_project(self):
        os.environ["MUSCLE_MEMORY_PROJECT_DIR"] = str(self.project_dir)
        from muscle_memory_mcp import trust
        trust.approve(self.project)


class TestMergedListing(LibrariesTestCase):
    def test_areas_labelled_from_both(self):
        make_lib(self.personal, "# idx\n\n## Shopping\n\na.py X — a [pure]\nb.py X — b [pure]\n")
        make_lib(self.project, "# idx\n\n## Build\n\nbuild.py — build it [pure]\n")
        self.set_project()
        out = core.list_areas()
        self.assertIn("- Shopping (2, personal)", out)
        self.assertIn("- Build (1, project)", out)

    def test_non_ascii_area_and_script_output_round_trip(self):
        # Review Focus 3: mm_list/mm_run must return correct UTF-8 text (å ä ö ✓), not mangled.
        make_lib(self.personal, "# idx\n\n## Hälsa\n\ntrain.py — öva styrka ✓ [pure]\n")
        (self.personal / "scripts" / "train.py").write_text("print('å ä ö ✓')\n", encoding="utf-8")
        out = core.list_areas()
        self.assertIn("- Hälsa (1, personal)", out)
        area_out = core.list_area("Hälsa")
        self.assertIn("öva styrka ✓", area_out)
        code, run_out, err = core.run("train.py", [])
        self.assertEqual(code, 0, err)
        self.assertEqual(run_out.strip(), "å ä ö ✓")

    def test_same_area_in_both_listed_per_library(self):
        make_lib(self.personal, "# idx\n\n## Shopping\n\np1.py — personal pick [pure]\n")
        make_lib(self.project, "# idx\n\n## Shopping\n\nq1.py — project pick [pure]\n")
        self.set_project()
        out = core.list_area("shopping")
        self.assertIn("## Shopping (personal)", out)
        self.assertIn("## Shopping (project)", out)
        self.assertIn("p1.py", out)
        self.assertIn("q1.py", out)


class TestRunPrecedence(LibrariesTestCase):
    def test_project_wins_on_run_and_shadow_label(self):
        make_lib(self.personal, "# idx\n\n## Shopping\n\nx.py — shared name [pure]\n")
        make_lib(self.project, "# idx\n\n## Shopping\n\nx.py — shared name [pure]\n")
        (self.personal / "scripts" / "x.py").write_text(PY_PRINT.format("P"), encoding="utf-8")
        (self.project / "scripts" / "x.py").write_text(PY_PRINT.format("Q"), encoding="utf-8")
        self.set_project()

        lib, p = core.resolve("x.py")
        self.assertEqual(lib.scope, "project")

        code, out, err = core.run("x.py", [])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "Q")

        area_out = core.list_area("Shopping")
        sections = area_out.split("\n\n")
        personal_section = next(s for s in sections if s.startswith("## Shopping (personal)"))
        project_section = next(s for s in sections if s.startswith("## Shopping (project)"))
        self.assertIn("x.py — shared name [pure] (shadowed)", personal_section)
        self.assertNotIn("(shadowed)", project_section)


class TestHomeIsNotAProject(LibrariesTestCase):
    def test_home_cwd_is_not_a_project(self):
        make_lib(self.personal, "# idx\n\n## Shopping\n\na.py X — a [pure]\n")
        os.chdir(self.home)
        self.assertIsNone(paths.project_root())
        out = core.list_areas()
        self.assertIn("- Shopping (1, personal)", out)
        self.assertNotIn("project)", out)


class TestProjectRootGuardDistinctFromHome(LibrariesTestCase):
    """Review Focus 1's second guard: root/'.muscle-memory' resolving to the personal library,
    with cwd *not* the home directory (so the home-equality branch can't be what's firing)."""

    def test_personal_library_inside_cwd_is_not_also_a_project(self):
        os.environ["MUSCLE_MEMORY_DIR"] = str(self.project_dir / ".muscle-memory")
        make_lib(self.project_dir / ".muscle-memory", "# idx\n\n## Shopping\n\na.py X — a [pure]\n")
        os.chdir(self.project_dir)
        self.assertNotEqual(self.project_dir.resolve(), self.home.resolve())

        self.assertIsNone(paths.project_root())
        libs = paths.libraries()
        self.assertEqual(len(libs), 1)
        self.assertEqual(libs[0].scope, "personal")


class TestCliTestRulingA(LibrariesTestCase):
    """Ruling (a): `cli test` (no NAME) also covers the project library; with NAME, resolve()
    precedence picks which library's copy is tested."""

    def test_cli_test_all_covers_both_libraries(self):
        make_lib(self.personal, "# idx\n\n## Math\n\np.py — p [pure]\n")
        make_lib(self.project, "# idx\n\n## Math\n\nq.py — q [pure]\n")
        (self.personal / "scripts" / "p.py").write_text(PY_PRINT.format("P"), encoding="utf-8")
        (self.personal / "tests" / "test_p.py").write_text(
            "import os, subprocess, sys, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_run(self):\n"
            "        out = subprocess.run([sys.executable, os.environ['MM_SCRIPT']], capture_output=True, encoding='utf-8').stdout\n"
            "        self.assertEqual(out.strip(), 'P')\n"
            "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        (self.project / "scripts" / "q.py").write_text(PY_PRINT.format("Q"), encoding="utf-8")
        (self.project / "tests" / "test_q.py").write_text(
            "import os, subprocess, sys, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_run(self):\n"
            "        out = subprocess.run([sys.executable, os.environ['MM_SCRIPT']], capture_output=True, encoding='utf-8').stdout\n"
            "        self.assertEqual(out.strip(), 'Q')\n"
            "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        self.set_project()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["test"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertEqual(buf.getvalue(), "2 passed\n")

    def test_cli_test_name_resolves_via_precedence(self):
        make_lib(self.personal, "# idx\n\n## Math\n\nx.py — x [pure]\n")
        make_lib(self.project, "# idx\n\n## Math\n\nx.py — x [pure]\n")
        (self.personal / "scripts" / "x.py").write_text(PY_PRINT.format("P"), encoding="utf-8")
        (self.personal / "tests" / "test_x.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_fail(self):\n        self.fail('wrong library tested')\n"
            "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        (self.project / "scripts" / "x.py").write_text(PY_PRINT.format("Q"), encoding="utf-8")
        (self.project / "tests" / "test_x.py").write_text(
            "import os, subprocess, sys, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_run(self):\n"
            "        out = subprocess.run([sys.executable, os.environ['MM_SCRIPT']], capture_output=True, encoding='utf-8').stdout\n"
            "        self.assertEqual(out.strip(), 'Q')\n"
            "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        self.set_project()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["test", "x.py"])
        self.assertEqual(code, 0, buf.getvalue())  # the project's test.py ran, not the personal one


class TestIndexCrlfAndUtf8(unittest.TestCase):
    def test_crlf_index_parses_and_is_preserved(self):
        text = "# idx\r\n\r\n## Shopping\r\n\r\na.py X [pure]\r\n"
        entries = index.parse_index(text)
        self.assertEqual([(e["area"], e["script"]) for e in entries], [("Shopping", "a.py")])

        result = index.add_index_line(text, "Shopping", "b.py Y [pure]")
        self.assertIn("b.py Y [pure]", result)
        self.assertEqual(result.count("\n"), result.count("\r\n"))  # every \n is part of \r\n

    def test_new_entry_is_separated_by_a_blank_line(self):
        # Markdown merges adjacent lines into one paragraph; a blank line keeps entries apart.
        text = "# idx\n\n## Shopping\n\na.py X [pure]\n\n## Other\n\nc.py Z [pure]\n"
        result = index.add_index_line(text, "Shopping", "b.py Y [pure]")
        self.assertEqual(result, "# idx\n\n## Shopping\n\na.py X [pure]\n\nb.py Y [pure]\n\n## Other\n\nc.py Z [pure]\n")
        last = index.add_index_line(text, "Other", "d.py W [pure]")
        self.assertTrue(last.endswith("c.py Z [pure]\n\nd.py W [pure]\n"))

    def test_utf8_index_roundtrip(self):
        text = "# idx\n\n## Hälsa\n\ntrain.py — öva [pure]\n"
        entries = index.parse_index(text)
        self.assertEqual([(e["area"], e["script"]) for e in entries], [("Hälsa", "train.py")])


if __name__ == "__main__":
    unittest.main()
