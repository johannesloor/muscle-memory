import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from muscle_memory_mcp import runners


class TestRunnerFor(unittest.TestCase):
    def test_ps1_prefers_pwsh(self):
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/pwsh" if name == "pwsh" else None):
            self.assertEqual(runners.runner_for(Path("a.ps1"))[1:], ["-NoProfile", "-File"])

    def test_ps1_falls_back_to_windows_powershell(self):
        which = lambda name: "C:/Windows/System32/powershell.exe" if name == "powershell" else None
        with patch("shutil.which", side_effect=which):
            with patch("sys.platform", "win32"):
                argv = runners.runner_for(Path("a.ps1"))
                self.assertEqual(argv[1:], ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            with patch("sys.platform", "darwin"):
                with self.assertRaises(ValueError):
                    runners.runner_for(Path("a.ps1"))

    def test_cmd_only_on_windows(self):
        which = lambda name: "C:/Windows/System32/cmd.exe" if name == "cmd" else None
        with patch("shutil.which", side_effect=which):
            with patch("sys.platform", "win32"):
                self.assertEqual(runners.runner_for(Path("a.cmd")),
                                  ["C:/Windows/System32/cmd.exe", "/c"])
            with patch("sys.platform", "darwin"):
                with self.assertRaises(ValueError):
                    runners.runner_for(Path("a.cmd"))

    def test_missing_runner_message(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError) as cm:
                runners.runner_for(Path("a.lua"))
        self.assertIn("runner 'lua' not found on PATH", str(cm.exception))


class TestRunProcess(unittest.TestCase):
    def test_utf8_output(self):
        script = Path(__file__).resolve().parent / "_utf8_echo.py"
        script.write_text("print('å ä ö ✓')\n", encoding="utf-8")
        try:
            code, out, err = runners.run_process([sys.executable, str(script)], Path.cwd(), None, 30)
        finally:
            script.unlink()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "å ä ö ✓\n")


if __name__ == "__main__":
    unittest.main()
