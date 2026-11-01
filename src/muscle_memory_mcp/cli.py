"""Command-line interface for muscle-memory-mcp.

  muscle-memory-mcp                     run the MCP server (stdio)
  muscle-memory-mcp list [AREA]          without AREA: all areas with script counts; with AREA: its scripts
  muscle-memory-mcp run SCRIPT [ARGS...] run a library script, passing ARGS through
  muscle-memory-mcp save NAME --code F --test F --area AREA --usage TEXT [--new-area] [--no-push]
                                         add a script + test, run the suite, index, commit
  muscle-memory-mcp setup [--target personal|project] [options]  set up a library
  muscle-memory-mcp test [NAME]         without NAME: run every script's own test; with NAME: just that one
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import core, onboarding, paths, server, testing


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    for stream in (sys.stdout, sys.stderr):  # scripts may print any Unicode; never crash on a code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if not argv:
        server.main()
        return 0
    ap = argparse.ArgumentParser(prog="muscle-memory-mcp", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("area", nargs="*")
    r = sub.add_parser("run"); r.add_argument("script"); r.add_argument("args", nargs=argparse.REMAINDER)
    v = sub.add_parser("save"); v.add_argument("name")
    v.add_argument("--code", required=True); v.add_argument("--test", required=True)
    v.add_argument("--area", required=True); v.add_argument("--usage", required=True)
    v.add_argument("--scope", choices=("personal", "project"), default="personal")
    v.add_argument("--replace", action="store_true")
    push_options = v.add_mutually_exclusive_group()
    push_options.add_argument("--push", action="store_true", help="push this save even if setup chose not to")
    push_options.add_argument("--no-push", action="store_true", help="don't push this save even if setup chose to")
    v.add_argument("--new-area", action="store_true", help="allow creating AREA if it does not exist")
    v.add_argument("--test-name")
    v.add_argument("--test-runner", help="JSON argv for the test interpreter")
    v.add_argument("--runner", help="JSON array argv for a custom interpreter, e.g. '[\"bash\"]' or "
                                    "'[\"deno\", \"run\"]'; only needed for extensions with no built-in runner")
    setup = sub.add_parser("setup")
    setup.add_argument("--target", choices=("personal", "project"), default="personal")
    setup.add_argument("--mode", choices=("local", "existing", "clone"))
    setup.add_argument("--path"); setup.add_argument("--remote")
    setup.add_argument("--git", action="store_true")
    setup.add_argument("--shared", action=argparse.BooleanOptionalAction, default=None,
                       help="project library: --shared commits it with the project, --no-shared keeps it private")
    setup.add_argument("--push", action=argparse.BooleanOptionalAction, default=None,
                       help="personal git library: push scripts to the git remote automatically")
    t = sub.add_parser("test"); t.add_argument("name", nargs="?",
                                                help="one script's file name; omit to test every saved script")
    trust_cmd = sub.add_parser("trust", help="review and explicitly approve a project library")
    trust_cmd.add_argument("--project", action="store_true", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "trust":
            project = paths.project_root()
            if project is None:
                raise ValueError("no project library here")
            root = (project / ".muscle-memory").resolve()
            if not (root / "INDEX.md").is_file():
                raise ValueError("no project library here")
            from . import trust
            trust.approve(root)
            print(f"Trusted project library {root}.")
            return 0
        if a.cmd == "setup":
            print(server.call("mm_setup", {"target": a.target, "mode": a.mode, "path": a.path,
                                            "remote": a.remote, "git": a.git, "shared": a.shared,
                                            "push": a.push})[0])
            return 0
        if a.cmd == "list":
            text = core.list_areas() if not a.area else core.list_area(" ".join(a.area))
            print(text)
            return 3 if text == onboarding.NOT_SET_UP else 0
        if a.cmd == "run":
            code, out, err = core.run(a.script, a.args)
            sys.stdout.write(out); sys.stderr.write(err)
            return code
        if a.cmd == "test":
            if a.name:
                lib, _ = core.resolve(a.name)
                jobs = [(lib, a.name)]
            else:
                jobs = []
                for lib in paths.libraries():
                    scripts_dir = lib.root / "scripts"
                    if not scripts_dir.exists():
                        continue
                    names = sorted(p.name for p in scripts_dir.iterdir()
                                    if p.is_file() and not p.name.startswith("test_")
                                    and not p.name.endswith(".runner.json") and not p.name.endswith(".test.json"))
                    jobs.extend((lib, n) for n in names)
            passed, failed = 0, []
            for lib, n in jobs:
                ok, msg = testing.run_test(lib.root, n)
                if ok:
                    passed += 1
                else:
                    failed.append(n)
                    print(f"FAIL {n}\n{msg}")
            print(f"{passed} passed" + (f", {len(failed)} failed" if failed else ""))
            return 1 if failed else 0
        runner = None
        test_runner = None
        for option, raw in (("--runner", a.runner), ("--test-runner", a.test_runner)):
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ValueError(f"{option} must be a JSON array of strings: {e}")
                if option == "--runner": runner = parsed
                else: test_runner = parsed
        ok, msg = core.save(a.name, Path(a.code).read_text(encoding="utf-8"),
                            Path(a.test).read_text(encoding="utf-8"), a.area, a.usage,
                            scope=a.scope, new_area=a.new_area, runner=runner,
                            test_name=a.test_name, test_runner=test_runner,
                            replace=a.replace, push=True if a.push else False if a.no_push else None)
        print(msg)
        if msg == onboarding.NOT_SET_UP:
            return 3
        return 0 if ok else 1
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        if str(e) == onboarding.NOT_SET_UP:
            print(onboarding.NOT_SET_UP)
            return 3
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
