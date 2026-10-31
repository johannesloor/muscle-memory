"""End-to-end smoke test: a real muscle_memory_mcp stdio subprocess, start to finish.

Isolates everything from the host (ruling a): a fresh MUSCLE_MEMORY_DIR (left non-existent, so
the first call hits the 'not set up' path), fresh HOME/USERPROFILE carrying a throwaway git
identity (mm_setup(git=True) and mm_save both run `git commit`, and the real/global git config
must never be touched), a cwd that is NOT a git repo (so no project library interferes with the
personal-library-only flow under test), and MUSCLE_MEMORY_PROJECT_DIR unset.

Ruling (b): mm_run is called with the exact saved script name (e.g. 'greet.sh'), never a glob.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from muscle_memory_mcp import onboarding

IS_WINDOWS = sys.platform == "win32"


def _native_runner_available():
    if IS_WINDOWS:
        return bool(shutil.which("pwsh") or shutil.which("powershell"))
    return bool(shutil.which("bash"))


NATIVE_RUNNER_SKIP_REASON = (
    "neither pwsh nor powershell found on PATH" if IS_WINDOWS else "bash not found on PATH"
)

if IS_WINDOWS:
    SCRIPT_NAME = "greet.ps1"
    SCRIPT_CODE = "param($name)\nWrite-Output \"hi $name\"\n"
    # Mirrors runners._builtin_argv_prefix's own pwsh-then-powershell fallback, so the test
    # keeps working on a runner that only has Windows PowerShell installed.
    TEST_CODE = (
        "$runner = if (Get-Command pwsh -ErrorAction SilentlyContinue) { 'pwsh' } else { 'powershell' }\n"
        "$out = & $runner -NoProfile -File $env:MM_SCRIPT Jo\n"
        "if ($out -ne 'hi Jo') { exit 1 }\n"
        "exit 0\n"
    )
else:
    SCRIPT_NAME = "greet.sh"
    SCRIPT_CODE = "#!/bin/bash\necho \"hi $1\"\n"
    TEST_CODE = "#!/bin/bash\n[ \"$(bash \"$MM_SCRIPT\" Jo)\" = \"hi Jo\" ]\n"

USAGE = f"{SCRIPT_NAME} NAME \u2014 demo greeting [pure]"


class E2ESmokeTest(unittest.TestCase):
    """Starts `python -m muscle_memory_mcp` as a real stdio subprocess, end to end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib_dir = root / "lib"  # MUSCLE_MEMORY_DIR; left non-existent -> NOT_SET_UP first
        self.home_dir = root / "home"
        self.cwd_dir = root / "cwd"  # not a git repo
        self.home_dir.mkdir()
        self.cwd_dir.mkdir()
        # A throwaway git identity local to the fake HOME only, so mm_setup(git=True) and
        # mm_save's commit succeed without touching the real user's or this repo's git config.
        (self.home_dir / ".gitconfig").write_text(
            "[user]\n\tname = Muscle Memory CI\n\temail = ci@example.invalid\n", encoding="utf-8")

        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        self.env["MUSCLE_MEMORY_DIR"] = str(self.lib_dir)
        self.env["HOME"] = str(self.home_dir)
        self.env["USERPROFILE"] = str(self.home_dir)
        self.env.pop("MUSCLE_MEMORY_PROJECT_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def _roundtrip(self, requests):
        proc = subprocess.run(
            [sys.executable, "-m", "muscle_memory_mcp"],
            input="\n".join(json.dumps(r) for r in requests) + "\n",
            capture_output=True, encoding="utf-8", timeout=60, env=self.env, cwd=str(self.cwd_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]

    @unittest.skipUnless(_native_runner_available(), NATIVE_RUNNER_SKIP_REASON)
    def test_full_lifecycle(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "mm_list", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "mm_setup",
                        "arguments": {"target": "personal", "mode": "local", "git": True}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "mm_save", "arguments": {
                 "name": SCRIPT_NAME, "code": SCRIPT_CODE, "test": TEST_CODE,
                 "area": "Demo", "usage": USAGE, "new_area": True}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "mm_run", "arguments": {"script": SCRIPT_NAME, "args": ["Jo"]}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "mm_list", "arguments": {"area": "Demo"}}},
        ]
        replies = self._roundtrip(requests)

        # notifications/initialized has no id and gets no reply.
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5, 6, 7])

        init_result = replies[0]["result"]
        self.assertEqual(init_result["serverInfo"]["name"], "muscle-memory")

        tools = replies[1]["result"]["tools"]
        self.assertEqual(len(tools), 4)
        self.assertEqual({t["name"] for t in tools}, {"mm_list", "mm_run", "mm_save", "mm_setup"})

        not_set_up = replies[2]["result"]
        self.assertFalse(not_set_up["isError"], not_set_up)
        self.assertEqual(not_set_up["content"][0]["text"], onboarding.NOT_SET_UP)

        setup_result = replies[3]["result"]
        self.assertFalse(setup_result["isError"], setup_result)
        # self.lib_dir may be a short (8.3) path on Windows (e.g. RUNNER~1); the product
        # reports the resolved long path, so compare against the resolved form too.
        self.assertIn(str(self.lib_dir.resolve()), setup_result["content"][0]["text"])
        self.assertTrue((self.lib_dir / ".git").exists())

        save_result = replies[4]["result"]
        self.assertFalse(save_result["isError"], save_result)
        self.assertIn("saved, tests pass", save_result["content"][0]["text"])

        run_result = replies[5]["result"]
        self.assertFalse(run_result["isError"], run_result)
        self.assertEqual(run_result["content"][0]["text"], "hi Jo")

        list_result = replies[6]["result"]
        self.assertFalse(list_result["isError"], list_result)
        self.assertIn(USAGE, list_result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
