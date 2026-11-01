"""Regression tests for the pre-release review: protocol robustness, git argument injection,
UTF-8 stdio, clear argument errors, and previously unexercised store/runner/CLI branches."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from muscle_memory_mcp import cli, core, onboarding, runners, server, store

ROOT = Path(__file__).resolve().parent.parent
CODE = "print('ok')\n"
GOOD_TEST = ("import os, subprocess, sys\n"
             "assert subprocess.check_output([sys.executable, os.environ['MM_SCRIPT']]).decode().strip() == 'ok'\n")
INDEX = "# Index\n\n## General\n\nx.py — old usage [pure]\n\n## Other\n\nz.py — zed [pure]\n"


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        from muscle_memory_mcp import trust
        self._trust_path = mock.patch.object(trust, "_state_path", return_value=self.base / "trust.json")
        self._trust_path.start()
        self.personal = self.base / "personal"
        self.project = self.base / "project"
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

    def library(self, index=INDEX):
        for sub in ("scripts", "tests"):
            (self.personal / sub).mkdir(parents=True, exist_ok=True)
        (self.personal / "INDEX.md").write_text(index, encoding="utf-8")

    def stdio(self, lines, env=None):
        return subprocess.run([sys.executable, "-m", "muscle_memory_mcp"], input="\n".join(lines) + "\n",
                              capture_output=True, encoding="utf-8", timeout=30,
                              env={**os.environ, **(env or {})})


class TestProtocolRobustness(Sandbox):
    def test_non_object_messages_get_invalid_request_and_server_keeps_running(self):
        r = self.stdio(["42", '"x"', "true", "null",
                        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})])
        self.assertEqual(r.returncode, 0, r.stderr)
        replies = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual([x.get("error", {}).get("code") for x in replies[:4]], [-32600] * 4)
        self.assertTrue(all(x["id"] is None for x in replies[:4]))
        self.assertEqual(replies[4], {"jsonrpc": "2.0", "id": 9, "result": {}})

    def test_handle_rejects_non_object_and_wrong_version(self):
        self.assertEqual(server.handle(7)["error"]["code"], -32600)
        bad = server.handle({"jsonrpc": "1.0", "id": 3, "method": "ping"})
        self.assertEqual((bad["id"], bad["error"]["code"]), (3, -32600))

    def test_unexpected_handler_crash_becomes_internal_error_not_exit(self):
        with mock.patch.object(server, "handle", side_effect=RuntimeError("boom")):
            out = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')), \
                    contextlib.redirect_stdout(out):
                server.main()
        reply = json.loads(out.getvalue())
        self.assertEqual((reply["id"], reply["error"]["code"]), (1, -32603))

    def test_stdin_is_read_as_utf8_even_with_a_legacy_default_encoding(self):
        self.library()
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "mm_list", "arguments": {"area": "Hälsa — Ё"}}}
        r = self.stdio([json.dumps(req, ensure_ascii=False)], env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(r.returncode, 0, r.stderr)
        text = json.loads(r.stdout.splitlines()[0])["result"]["content"][0]["text"]
        self.assertIn("Hälsa — Ё", text)


class TestArgumentErrors(Sandbox):
    def test_missing_required_arguments_are_named(self):
        self.library()
        for tool, args, missing in (("mm_save", {}, "name"),
                                    ("mm_save", {"name": "a.py", "code": "", "test": "", "area": "General"}, "usage"),
                                    ("mm_run", {}, "script")):
            reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": tool, "arguments": args}})
            self.assertTrue(reply["result"]["isError"])
            self.assertIn(f"missing required argument '{missing}'", reply["result"]["content"][0]["text"])

    def test_setup_project_through_dispatch(self):
        self.project.mkdir()
        text, is_err = server.call("mm_setup", {"target": "project"})
        self.assertFalse(is_err)
        self.assertIn("Project library ready at", text)
        self.assertTrue((self.project / ".muscle-memory" / "INDEX.md").is_file())


class TestCloneInjection(Sandbox):
    def test_option_like_remote_is_rejected_and_runs_nothing(self):
        marker = self.base / "pwned"
        for remote in (f"--upload-pack=touch {marker};", "-u touch", " --template=x"):
            with self.assertRaisesRegex(ValueError, "must not start with '-'"):
                onboarding.setup_personal("clone", str(self.base / "dest"), remote=remote)
        self.assertFalse(marker.exists())
        self.assertFalse((self.base / "dest").exists())

    def test_clone_ends_options_and_limits_protocols(self):
        with mock.patch.object(onboarding.subprocess, "run") as run, \
                mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/git"):
            onboarding.setup_personal("clone", str(self.base / "dest"), remote="https://example.invalid/lib.git")
        argv, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(argv[:3], ["git", "clone", "--"])
        self.assertEqual(argv[3], "https://example.invalid/lib.git")
        self.assertNotIn("ext", kwargs["env"]["GIT_ALLOW_PROTOCOL"].split(":"))


class TestStoreBranches(Sandbox):
    def test_git_add_ends_options_before_paths(self):
        self.library()
        (self.personal / "scripts" / "x.py").write_text(CODE, encoding="utf-8")
        calls = []
        with mock.patch.object(store.shutil, "which", return_value="/usr/bin/git"), \
                mock.patch.object(store, "_run_git", side_effect=lambda root, *a, **k: calls.append(a) or (0, "", "")):
            store._save_personal_git(self.personal, "x.py", "x.py — u", ["scripts/x.py", "INDEX.md"], False, False)
        add = next(a for a in calls if a[0] == "add")
        self.assertEqual(add, ("add", "--", "scripts/x.py", "INDEX.md"))

    def test_replace_into_another_area_moves_the_entry(self):
        self.library()
        (self.personal / "scripts" / "x.py").write_text(CODE, encoding="utf-8")
        (self.personal / "tests" / "test_x.py").write_text(GOOD_TEST, encoding="utf-8")
        ok, msg = store.save("x.py", CODE, GOOD_TEST, "Other", "x.py — moved [pure]", replace=True, push=False)
        self.assertTrue(ok, msg)
        text = (self.personal / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(text, "# Index\n\n## General\n\n## Other\n\nz.py — zed [pure]\n\nx.py — moved [pure]\n")
        self.assertEqual(core.areas(), [("Other", 2)])

    def test_bad_test_names_are_rejected(self):
        self.library()
        for bad in ("../test_new.py", "sub/test_new.py", "new_test.py", "test_other.py", "test_new", "test_new.PY"):
            with self.assertRaisesRegex(ValueError, "test_name must be"):
                store.save("new.py", CODE, GOOD_TEST, "General", "new.py — n [pure]", test_name=bad, push=False)
        self.assertFalse((self.personal / "scripts" / "new.py").exists())


class TestRunnerSidecar(Sandbox):
    def test_malformed_runner_json_is_rejected(self):
        self.library()
        script = self.personal / "scripts" / "x.sh"
        script.write_text("echo hi\n", encoding="utf-8")
        for content in ("[\"bash\"]", "\"bash\"", "{\"runner\": [\"bash\"]}"):
            runners.runner_metadata_path(script).write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object with an 'argv' list"):
                runners.runner_for(script)

    def test_public_helpers_replace_private_cross_module_use(self):
        self.assertTrue(callable(runners.resolve_executable))
        self.assertTrue(callable(runners.builtin_argv_prefix))
        src = (ROOT / "src" / "muscle_memory_mcp" / "testing.py").read_text(encoding="utf-8")
        self.assertNotIn("runners._", src)


class TestCliHappyPaths(Sandbox):
    def cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def files(self, code, test):
        c, t = self.base / "code.txt", self.base / "test.txt"
        c.write_text(code, encoding="utf-8")
        t.write_text(test, encoding="utf-8")
        return str(c), str(t)

    def test_save_list_run_and_exit_code_passthrough(self):
        self.library()
        code = "import sys\nprint('hi ' + ' '.join(sys.argv[1:]))\nsys.exit(3)\n"
        test = ("import os, subprocess, sys\n"
                "r = subprocess.run([sys.executable, os.environ['MM_SCRIPT'], 'Jo'], capture_output=True, text=True)\n"
                "assert r.returncode == 3 and r.stdout.strip() == 'hi Jo'\n")
        c, t = self.files(code, test)
        rc, out, err = self.cli(["save", "greet.py", "--code", c, "--test", t, "--area", "General",
                                 "--usage", "greet.py NAME — greets, exits 3 [pure]", "--no-push"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("tests pass", out)
        rc, out, _ = self.cli(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("- General (2", out)
        rc, out, _ = self.cli(["list", "General"])
        self.assertEqual(rc, 0)
        self.assertIn("greet.py NAME — greets, exits 3 [pure]", out)
        rc, out, _ = self.cli(["run", "greet.py", "Jo"])
        self.assertEqual((rc, out.strip()), (3, "hi Jo"))

    def test_save_with_failing_test_exits_1(self):
        self.library()
        c, t = self.files(CODE, "raise SystemExit(1)\n")
        rc, out, _ = self.cli(["save", "bad.py", "--code", c, "--test", t, "--area", "General",
                               "--usage", "bad.py — x [pure]", "--no-push"])
        self.assertEqual(rc, 1)
        self.assertFalse((self.personal / "scripts" / "bad.py").exists())

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_save_with_runner_json_and_bad_runner_json(self):
        self.library()
        c, t = self.files("echo hi\n", "import os, subprocess, shutil\n"
                                       "assert subprocess.check_output([shutil.which('bash'), os.environ['MM_SCRIPT']]).decode().strip() == 'hi'\n")
        base = ["save", "hi.zsh", "--code", c, "--test", t, "--area", "General", "--usage", "hi.zsh — hi [pure]",
                "--no-push", "--test-name", "test_hi.py", "--test-runner", json.dumps([sys.executable])]
        rc, out, err = self.cli(base + ["--runner", "not json"])
        self.assertEqual(rc, 2)
        self.assertIn("--runner must be a JSON array of strings", err)
        rc, out, err = self.cli(base + ["--runner", '["bash"]'])
        self.assertEqual(rc, 0, out + err)
        rc, out, _ = self.cli(["run", "hi.zsh"])
        self.assertEqual((rc, out.strip()), (0, "hi"))


class TestProjectTrustGate(Sandbox):
    def setUp(self):
        super().setUp()
        self.project.mkdir()
        self.lib = self.project / ".muscle-memory"
        self.personal.mkdir(parents=True)
        for sub in ("scripts", "tests"):
            (self.lib / sub).mkdir(parents=True)
        (self.lib / "INDEX.md").write_text("# Index\\n\\n## General\\n\\nx.py — x [pure]\\n", encoding="utf-8")
        (self.lib / "scripts/x.py").write_text("print('ran')\\n", encoding="utf-8")
        (self.lib / "tests/test_x.py").write_text("pass\\n", encoding="utf-8")

    def test_project_run_requires_external_trust_before_launch(self):
        from muscle_memory_mcp import core
        with mock.patch.object(runners, "run_process") as launch:
            with self.assertRaisesRegex(ValueError, "not trusted"):
                core.run("x.py", [])
        launch.assert_not_called()

    def test_project_content_change_keeps_trust(self):
        from muscle_memory_mcp import trust
        trust.approve(self.lib)
        (self.lib / "tests/test_x.py").write_text("print('changed')\\n", encoding="utf-8")
        trust.require(self.lib)

    def test_project_forged_in_tree_trust_marker_is_ignored(self):
        (self.lib / ".trusted").write_text("yes", encoding="utf-8")
        from muscle_memory_mcp import trust
        with self.assertRaisesRegex(ValueError, "not trusted"):
            trust.require(self.lib)

    def test_project_cli_trust_command_approves_resolved_library(self):
        from muscle_memory_mcp import trust
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["trust", "--project"])
        self.assertEqual(rc, 0, out.getvalue() + err.getvalue())
        trust.require(self.lib)


class TestReleaseGate(unittest.TestCase):
    def test_release_runs_the_full_ci_matrix_before_publishing(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        rel = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", ci)
        self.assertIn("-W error::EncodingWarning", ci)
        self.assertRegex(rel, r"test:\n\s+uses: \./\.github/workflows/ci\.yml")
        self.assertRegex(rel, r"publish-pypi:\n\s+needs: test")


class TestRound2Protocol(Sandbox):
    def init(self, version):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": version}})["result"]["protocolVersion"]

    def test_initialize_negotiates_a_supported_version(self):
        for v in server.SUPPORTED_PROTOCOLS:
            self.assertEqual(self.init(v), v)
        self.assertEqual(self.init("1999-01-01"), server.SUPPORTED_PROTOCOLS[0])
        self.assertEqual(self.init(None), server.SUPPORTED_PROTOCOLS[0])

    def test_missing_method_is_invalid_request(self):
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 4})["error"]["code"], -32600)

    def test_non_object_params_and_arguments_are_tool_errors_not_crashes(self):
        bad = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": [1]})
        self.assertEqual(bad["error"]["code"], -32602)
        r = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "mm_list", "arguments": "x"}})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": 7})["error"]["code"], -32602)

    def test_batch_requests_get_a_batch_reply(self):
        r = self.stdio([json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                    5]),
                        "[]",
                        json.dumps([{"jsonrpc": "2.0", "method": "notifications/initialized"}])])
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual(len(lines), 2)  # all-notification batch gets no reply at all
        self.assertEqual(lines[0][0], {"jsonrpc": "2.0", "id": 1, "result": {}})
        self.assertEqual(lines[0][1]["error"]["code"], -32600)
        self.assertEqual(len(lines[0]), 2)
        self.assertEqual(lines[1]["error"]["code"], -32600)  # empty batch


class TestRound2Git(Sandbox):
    def test_every_store_git_call_limits_protocols(self):
        self.assertNotIn("ext", store.GIT_ENV["GIT_ALLOW_PROTOCOL"].split(":"))
        self.assertEqual(store.GIT_ENV["GIT_ALLOW_PROTOCOL"], onboarding.GIT_ALLOW_PROTOCOL)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_push_to_ext_remote_is_refused_by_real_git_even_if_repo_allows_it(self):
        self.library()
        marker = self.base / "pwned"
        helper = self.base / "helper.py"
        helper.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        g = lambda *a: subprocess.run(["git", "-C", str(self.personal), *a], check=True, capture_output=True)
        g("init", "-q"); g("config", "user.name", "T"); g("config", "user.email", "t@t.invalid")
        g("config", "protocol.ext.allow", "always")
        g("commit", "--allow-empty", "-qm", "init")
        g("remote", "add", "origin", f"ext::{sys.executable} {helper}")
        g("config", "push.default", "current")
        ok, msg = store.save("new.py", CODE, GOOD_TEST, "General", "new.py — n [pure]", push=True)
        self.assertTrue(ok, msg)
        self.assertIn("push failed", msg)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_clone_from_ext_remote_is_refused_by_real_git(self):
        marker = self.base / "pwned"
        helper = self.base / "helper.py"
        helper.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.ext.allow",
                                          "GIT_CONFIG_VALUE_0": "always"}):
            with self.assertRaises(Exception):
                onboarding.setup_personal("clone", str(self.base / "dest"), remote=f"ext::{sys.executable} {helper}")
        self.assertFalse(marker.exists())


class TestRound2IndexMoves(Sandbox):
    def test_moving_a_middle_entry_keeps_single_blank_lines(self):
        text = "# I\n\n## A\n\na.py — a\n\nb.py — b\n\nc.py — c\n\n## B\n\nd.py — d\n"
        out = store._replace_index_entry(text, "b.py", "B", "b.py — moved")
        self.assertEqual(out, "# I\n\n## A\n\na.py — a\n\nc.py — c\n\n## B\n\nd.py — d\n\nb.py — moved\n")

    def test_moving_an_entry_in_a_crlf_index_keeps_crlf_and_single_blanks(self):
        text = "# I\r\n\r\n## A\r\n\r\na.py — a\r\n\r\nb.py — b\r\n\r\n## B\r\n\r\nd.py — d\r\n"
        out = store._replace_index_entry(text, "a.py", "B", "a.py — moved")
        self.assertEqual(out, "# I\r\n\r\n## A\r\n\r\nb.py — b\r\n\r\n## B\r\n\r\nd.py — d\r\n\r\na.py — moved\r\n")


class TestRound2Cli(TestCliHappyPaths):
    def test_cli_streams_are_utf8_with_a_legacy_default_encoding(self):
        self.library()
        c, t = self.files("print('Hälsa — Ё ✓')\n", "import os, subprocess, sys\n"
                          "assert 'Ё' in subprocess.check_output([sys.executable, os.environ['MM_SCRIPT']], encoding='utf-8')\n")
        rc, out, err = self.cli(["save", "uni.py", "--code", c, "--test", t, "--area", "General",
                                 "--usage", "uni.py — unicode [pure]", "--no-push"])
        self.assertEqual(rc, 0, out + err)
        r = subprocess.run([sys.executable, "-m", "muscle_memory_mcp", "run", "uni.py"], capture_output=True,
                           timeout=60, env={**os.environ, "PYTHONIOENCODING": "cp1252"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode("utf-8").strip(), "Hälsa — Ё ✓")

    def test_setup_project_private_from_cli(self):
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        rc, out, err = self.cli(["setup", "--target", "project", "--no-shared"])
        self.assertEqual(rc, 0, out + err)
        self.assertFalse((self.project / ".muscle-memory" / ".shared").exists())
        self.assertIn("private", out.lower())


class TestRound2WindowsBatch(Sandbox):
    def test_cmd_metacharacters_in_batch_args_are_rejected(self):
        self.library()
        (self.personal / "scripts" / "hi.bat").write_text("@echo hi %1\r\n", encoding="utf-8")
        for arg in ("a & calc", "a | b", "a > f", "%PATH%", 'x"y', "a ^ b", "!x!", "a\nb", "a < b"):
            with self.assertRaisesRegex(ValueError, "which cmd.exe would interpret"):
                core.run("hi.bat", [arg])

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_batch_script_runs_safe_args_on_windows(self):
        self.library()
        (self.personal / "scripts" / "hi.bat").write_text("@echo hi %~1\r\n", encoding="utf-8")
        code, out, err = core.run("hi.bat", ["Jo Lo"])
        self.assertEqual((code, out.strip()), (0, "hi Jo Lo"), err)


class TestRound2MissingTestFile(Sandbox):
    def test_missing_test_file_is_named(self):
        self.library()
        (self.personal / "scripts" / "lonely.py").write_text(CODE, encoding="utf-8")
        from muscle_memory_mcp import testing
        with self.assertRaisesRegex(ValueError, "no test file found for lonely.py"):
            testing.run_test(self.personal, "lonely.py")


class TestRound2ReleaseVersion(unittest.TestCase):
    def test_release_checks_tag_against_package_version_too(self):
        rel = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        pypi = rel[rel.index("publish-pypi:"):rel.index("publish-mcp-registry:")]
        check = pypi.index("__init__.py")
        self.assertLess(pypi.index("server.json"), pypi.index("Build sdist"))
        self.assertLess(check, pypi.index("Build sdist"))
        self.assertLess(check, pypi.index("gh-action-pypi-publish"))


class TestRound3(Sandbox):
    def test_clone_and_init_are_bounded_and_never_prompt(self):
        for mode, kw in (("clone", {"remote": "https://example.invalid/lib.git"}), ("local", {"git": True})):
            with mock.patch.object(onboarding.subprocess, "run") as run, \
                    mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/git"):
                onboarding.setup_personal(mode, str(self.base / f"dest-{mode}"), **kw)
            kwargs = run.call_args.kwargs
            self.assertGreater(kwargs["timeout"], 0, mode)
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0", mode)

    def test_clone_timeout_is_a_clear_error_and_leaves_nothing(self):
        dest = self.base / "dest"
        boom = subprocess.TimeoutExpired(["git", "clone"], 120)
        with mock.patch.object(onboarding.subprocess, "run", side_effect=boom), \
                mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/git"):
            with self.assertRaisesRegex(ValueError, "git clone timed out"):
                onboarding.setup_personal("clone", str(dest), remote="https://example.invalid/lib.git")
        self.assertFalse((dest / "INDEX.md").exists())

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_clone_failure_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "git clone failed"):
            onboarding.setup_personal("clone", str(self.base / "dest"), remote=str(self.base / "no-such-repo"))

    def test_newlines_in_area_or_usage_cannot_inject_index_structure(self):
        self.library()
        before = (self.personal / "INDEX.md").read_bytes()
        for area, usage in (("General\n\n## Evil", "x2.py — a [pure]"),
                            ("General", "x2.py — a [pure]\n\n## Deployment\n\nevil.sh — safe [pure]"),
                            ("General", "x2.py — a\r## Evil")):
            with self.assertRaisesRegex(ValueError, "single line"):
                store.save("x2.py", CODE, GOOD_TEST, area, usage, new_area=True, push=False)
        self.assertEqual((self.personal / "INDEX.md").read_bytes(), before)
        self.assertFalse((self.personal / "scripts" / "x2.py").exists())

    def test_mm_run_args_must_be_a_list_of_strings(self):
        self.library()
        for bad in ("ab", {"a": 1}, 5):
            with self.assertRaisesRegex(ValueError, "args must be a list of strings"):
                server.call("mm_run", {"script": "x.py", "args": bad})

    def test_cmd_runner_sidecar_on_other_extension_is_guarded(self):
        self.library()
        script = self.personal / "scripts" / "hi.xyz"
        script.write_text("@echo hi %1\r\n", encoding="utf-8")
        runners.runner_metadata_path(script).write_text('{"argv": ["cmd", "/c"]}', encoding="utf-8")
        with mock.patch.object(runners, "resolve_executable", return_value="C:/Windows/System32/cmd.exe"):
            with self.assertRaisesRegex(ValueError, "which cmd.exe would interpret"):
                core.run("hi.xyz", ["a & calc"])

    def test_supported_protocols_are_newest_first(self):
        self.assertEqual(list(server.SUPPORTED_PROTOCOLS), sorted(server.SUPPORTED_PROTOCOLS, reverse=True))


class TestRound4(Sandbox):
    def fail_clone(self, dest, exc):
        with mock.patch.object(onboarding.subprocess, "run", side_effect=exc), \
                mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/git"):
            def run(*a, **k):
                Path(dest, ".git").mkdir(parents=True, exist_ok=True)  # git leaves a partial clone
                raise exc
            onboarding.subprocess.run.side_effect = run
            with self.assertRaisesRegex(ValueError, "git clone (timed out|failed)"):
                onboarding.setup_personal("clone", str(dest), remote="https://example.invalid/lib.git")

    def test_failed_clone_into_new_dir_removes_it(self):
        for exc in (subprocess.TimeoutExpired(["git"], 1), subprocess.CalledProcessError(128, ["git"], stderr="x")):
            dest = self.base / f"new-{type(exc).__name__}"
            self.fail_clone(dest, exc)
            self.assertFalse(dest.exists())

    def test_failed_clone_into_existing_empty_dir_preserves_it(self):
        for exc in (subprocess.TimeoutExpired(["git"], 1), subprocess.CalledProcessError(128, ["git"], stderr="x")):
            dest = self.base / f"mine-{type(exc).__name__}"
            dest.mkdir()
            self.fail_clone(dest, exc)
            self.assertTrue(dest.is_dir())
            self.assertTrue((dest / ".git").is_dir())  # pre-existing target is never deleted from

    def test_git_init_failure_is_a_clear_error_in_server_and_cli(self):
        boom = subprocess.CalledProcessError(1, ["git", "init"], stderr="fatal: cannot mkdir")
        with mock.patch.object(onboarding.subprocess, "run", side_effect=boom), \
                mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/git"):
            with self.assertRaisesRegex(ValueError, "git init failed: fatal: cannot mkdir"):
                onboarding.setup_personal("local", str(self.base / "lib"), git=True)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(["setup", "--mode", "local", "--git"])
        self.assertEqual(rc, 2)
        self.assertIn("error: git init failed", err.getvalue())

    def test_cmd_guard_fires_before_any_launch(self):
        self.library()
        script = self.personal / "scripts" / "hi.xyz"
        script.write_text("@echo hi %1\r\n", encoding="utf-8")
        runners.runner_metadata_path(script).write_text('{"argv": ["cmd", "/c"]}', encoding="utf-8")
        with mock.patch.object(runners, "resolve_executable", return_value="C:/Windows/System32/cmd.exe"), \
                mock.patch.object(runners, "run_process") as launch:
            with self.assertRaisesRegex(ValueError, "which cmd.exe would interpret"):
                core.run("hi.xyz", ["a & calc"])
        launch.assert_not_called()

    def test_mm_run_args_type_check_with_a_real_script(self):
        self.library()
        (self.personal / "scripts" / "x.py").write_text(CODE, encoding="utf-8")
        with mock.patch.object(core, "run", return_value=(0, "ok", "")) as run:
            for bad in ("ab", {"a": 1}, 5, None, [["nested"]]):
                with self.subTest(args=bad):
                    with self.assertRaisesRegex(ValueError, "args must be a list of strings"):
                        server.call("mm_run", {"script": "x.py", "args": bad})
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
