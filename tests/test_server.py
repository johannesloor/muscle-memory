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

SHELL_CODE = "#!/bin/bash\necho \"hello $1\"\n"
# Test file keeps the script's own extension (greet.sh -> tests/test_greet.sh) and
# runs with the script's own runner (bash), per the language-native test contract.
SHELL_TEST = "#!/bin/bash\n[ \"$(bash \"$MM_SCRIPT\" World)\" = \"hello World\" ]\n"


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


class TestMcp(Lib):
    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_stdio_roundtrip(self):
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "mm_list", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "mm_run", "arguments": {"script": "../nope.py"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "mm_list", "arguments": {"area": "Shopping"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "mm_list", "arguments": {"area": "Nope"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "mm_save", "arguments": {
                 "name": "greet.sh", "code": SHELL_CODE, "test": SHELL_TEST,
                 "area": "Shopping", "usage": "greet.sh NAME — demo [pure]"}}},
        ]
        out = subprocess.run([sys.executable, "-m", "muscle_memory_mcp"],
                             input="\n".join(json.dumps(r) for r in reqs) + "\n",
                             capture_output=True, encoding="utf-8", timeout=30, env=os.environ).stdout
        replies = [json.loads(l) for l in out.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5, 6, 7])  # notification gets no reply
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "muscle-memory")
        self.assertIn("call mm_list before writing code", replies[0]["result"]["instructions"])
        self.assertEqual({t["name"] for t in replies[1]["result"]["tools"]}, {"mm_list", "mm_run", "mm_save", "mm_setup"})
        areas_text = replies[2]["result"]["content"][0]["text"]
        self.assertIn("- Shopping (1, personal)", areas_text)
        self.assertNotIn("product_page.py", areas_text)
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertIn("product_page.py URL", replies[4]["result"]["content"][0]["text"])
        self.assertTrue(replies[5]["result"]["isError"])
        self.assertIn("Areas: Files, Shopping", replies[5]["result"]["content"][0]["text"])
        mm_save_schema = next(t for t in replies[1]["result"]["tools"] if t["name"] == "mm_save")["inputSchema"]
        self.assertIn("runner", mm_save_schema["properties"])  # schema exposes the custom-runner override
        self.assertFalse(replies[6]["result"]["isError"], replies[6]["result"])
        self.assertIn("saved", replies[6]["result"]["content"][0]["text"])


class TextAndBudgetTests(unittest.TestCase):
    def test_four_tools_listed(self):
        self.assertEqual([t["name"] for t in server.TOOLS],
                          ["mm_list", "mm_run", "mm_save", "mm_setup"])

    def test_text_budget(self):
        combined = len(json.dumps(server.TOOLS)) + len(server.INSTRUCTIONS)
        self.assertLessEqual(combined, 3500, f"combined text is {combined} chars")

    def test_advertised_safety_and_schema_contract(self):
        blob = json.dumps(server.TOOLS) + server.INSTRUCTIONS
        for phrase in ("mm_list", "before", "project", "No matching", "--dry-run",
                       "irreversible", "explicit yes", "replace: true", "MM_SCRIPT",
                       "MM_LIBRARY", "exit 0", "saves nothing", "project never commits",
                       "after asking", "shared", "private"):
            self.assertIn(phrase.lower(), blob.lower())
        by_name = {t["name"]: t for t in server.TOOLS}
        self.assertEqual(set(by_name), {"mm_list", "mm_run", "mm_save", "mm_setup"})
        for name, required in {"mm_list": (), "mm_run": ("script",),
                               "mm_save": ("name", "code", "test", "area", "usage"),
                               "mm_setup": ()}.items():
            schema = by_name[name]["inputSchema"]
            self.assertEqual(tuple(schema.get("required", ())), required)
        props = {name: tool["inputSchema"]["properties"] for name, tool in by_name.items()}
        self.assertEqual(set(props["mm_list"]), {"area"})
        self.assertEqual(set(props["mm_run"]), {"script", "args"})
        self.assertEqual(set(props["mm_save"]), {"name", "code", "test", "test_name", "test_runner", "area", "usage", "new_area", "runner", "scope", "replace"})
        self.assertEqual(set(props["mm_setup"]), {"target", "mode", "path", "remote", "git", "shared", "push"})
        self.assertEqual(props["mm_setup"]["mode"]["enum"], ["local", "existing", "clone"])
        self.assertEqual(props["mm_setup"]["target"]["enum"], ["personal", "project"])
        self.assertEqual(props["mm_save"]["scope"]["enum"], ["personal", "project"])

    def test_no_python_lockin_in_text(self):
        blob = json.dumps(server.TOOLS) + server.INSTRUCTIONS
        for banned in ("python3", "always Python", "unittest", ".muscle-memory/tests"):
            self.assertNotIn(banned, blob)

    def test_instructions_cover_rules(self):
        for phrase in ("mm_list", "ask the user", "language that fits", "before ending your turn"):
            self.assertIn(phrase.lower(), server.INSTRUCTIONS.lower())

    def test_explicit_yes_gate_is_irreversible_only(self):
        """Fix round 1: the wait-for-explicit-yes gate must attach to [irreversible] only,
        not [write] — the binding ruling only gates irreversible scripts on explicit yes."""
        mm_run_desc = next(t for t in server.TOOLS if t["name"] == "mm_run")["description"]
        sentences = mm_run_desc.split(". ")
        gate_sentences = [s for s in sentences if "explicit yes" in s]
        self.assertTrue(gate_sentences, "no sentence mentions 'explicit yes'")
        for s in gate_sentences:
            self.assertIn("irreversible", s)
            self.assertNotIn("write", s)

    def test_initialize_negotiates_protocol_and_has_instructions(self):
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "2099-01-01"}})
        self.assertEqual(reply["result"]["protocolVersion"], server.SUPPORTED_PROTOCOLS[0])
        self.assertEqual(reply["result"]["instructions"], server.INSTRUCTIONS)


class SaveScopeReplaceStdioTests(Lib):
    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_save_scope_and_replace_roundtrip(self):
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "mm_save", "arguments": {
                 "name": "greet.sh", "code": SHELL_CODE, "test": SHELL_TEST,
                 "area": "Shopping", "usage": "greet.sh NAME — demo [pure]",
                 "scope": "personal"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "mm_save", "arguments": {
                 "name": "greet.sh", "code": SHELL_CODE, "test": SHELL_TEST,
                 "area": "Shopping", "usage": "greet.sh NAME — demo [pure]",
                 "scope": "personal"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "mm_save", "arguments": {
                 "name": "greet.sh", "code": SHELL_CODE, "test": SHELL_TEST,
                 "area": "Shopping", "usage": "greet.sh NAME — demo [pure]",
                 "scope": "personal", "replace": True}}},
        ]
        out = subprocess.run([sys.executable, "-m", "muscle_memory_mcp"],
                              input="\n".join(json.dumps(r) for r in reqs) + "\n",
                              capture_output=True, encoding="utf-8", timeout=30, env=os.environ).stdout
        replies = [json.loads(l) for l in out.splitlines()]
        self.assertFalse(replies[0]["result"].get("isError", False))
        self.assertFalse(replies[1]["result"]["isError"], replies[1]["result"])  # first save: ok
        self.assertTrue(replies[2]["result"]["isError"], replies[2]["result"])  # dup name, no replace: refused
        self.assertFalse(replies[3]["result"]["isError"], replies[3]["result"])  # replace: true: ok


class SetupValidationTests(Lib):
    """Fix round 1, finding 1: a typo'd target/mode must raise, not silently mis-route."""

    def test_invalid_target_raises_naming_allowed_values(self):
        with self.assertRaises(ValueError) as cm:
            server.call("mm_setup", {"target": "personnel", "mode": "local"})
        msg = str(cm.exception)
        self.assertIn("personal", msg)
        self.assertIn("project", msg)

    def test_invalid_mode_raises_naming_allowed_values(self):
        with self.assertRaises(ValueError) as cm:
            server.call("mm_setup", {"target": "personal", "mode": "locall"})
        msg = str(cm.exception)
        self.assertIn("local", msg)
        self.assertIn("existing", msg)
        self.assertIn("clone", msg)

    def test_missing_mode_raises(self):
        with self.assertRaises(ValueError):
            server.call("mm_setup", {"target": "personal"})


if __name__ == "__main__":
    unittest.main()
