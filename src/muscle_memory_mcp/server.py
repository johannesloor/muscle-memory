"""Muscle-memory MCP server (stdio, JSON-RPC 2.0, standard library only).

Exposes the library as tools so every harness that speaks MCP sees them on every step:
  mm_list, mm_run, mm_save.  Register as:  python -m muscle_memory_mcp
"""
import json
import sys

from . import __version__, core, onboarding, paths

PROTOCOL = "2024-11-05"

INSTRUCTIONS = (
    "Muscle memory: reusable scripts in personal/project libraries. At every repeatable task, call mm_list before writing code; run matches with mm_run. Otherwise use a language that fits, then save "
    "reusable work with mm_save. Tests use tests/test_<stem>.<ext>; exit 0 passes and receive MM_SCRIPT/MM_LIBRARY. Use test_name/test_runner for overrides. If not set up, ask the user once, then call mm_setup.")

TOOLS = [
    {
        "name": "mm_list",
        "description": (
            "Browse the user's library of tested, reusable scripts, across the personal and (if "
            "present) project library. Call this BEFORE writing code for a repeatable sub-step: "
            "fetching, scraping, parsing, converting, computing, or acting (sending, writing, "
            "controlling devices). Check again at each new sub-step. Call without 'area' to list "
            "areas with counts, e.g. 'Shopping (4, personal)'; then call with the area that fits to "
            "list its scripts. A name in both libraries runs from the project one, shadowing the "
            "personal one. No fitting area or script means there is none for this step — use mm_run "
            "on a match instead of writing code. If not set up, ask the user once, call mm_setup."),
        "inputSchema": {"type": "object", "properties": {
            "area": {"type": "string", "description": "Area name from the first call, e.g. 'Shopping'. Omit to list areas."}}},
    },
    {
        "name": "mm_run",
        "description": (
            "Run a script shown by mm_list, by file name, passing command-line args. The runner is "
            "picked by file extension or a saved runner; a name in both libraries runs the project "
            "one. Its output is the result to give the user. [write] scripts: run with --dry-run "
            "first and read back what it changed. [irreversible] scripts (send, buy, delete): run "
            "with --dry-run first, show that output, and wait for the user's explicit yes before "
            "the real, irreversible run. If it errors, repair it via mm_save (replace: true), not "
            "by hand-editing library files."),
        "inputSchema": {"type": "object", "properties": {
            "script": {"type": "string", "description": "file name, e.g. 'list_files.py'"},
            "args": {"type": "array", "items": {"type": "string"}, "default": []}}, "required": ["script"]},
    },
    {
        "name": "mm_save",
        "description": (
            "Add a new script, or repair/update one with replace: true. Use only after giving the "
            "user the result, once a snippet recurs or is clearly reusable; prefer replace: true on "
            "a close mm_list match over a duplicate. Pick a language fitting this platform and task, "
            "preferring its standard library; the runner is picked by file extension, or pass "
            "'runner' for anything else already installed. Inputs are arguments, not hard-coded; the "
            "script prints a short result; secrets come from environment variables. Write/act "
            "scripts take --dry-run and read back what changed. Tests may use any language: "
            "tests/test_<stem>.<ext>; exit 0 passes. By default they use the script extension and "
            "runner. Set test_name and optionally test_runner argv to choose independently. Tests "
            "receive MM_SCRIPT and MM_LIBRARY. A failing test saves nothing. Personal saves are "
            "committed to the library's Git repository (and pushed if it has a remote); project "
            "saves are written but never committed — the user commits them. Use scope: "
            "'project' only for scripts specific to this codebase. Refuses a name that exists in "
            "either library unless replace: true matches the same library."),
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "snake_case file name with the language's extension"},
            "code": {"type": "string", "description": "full script source, language fitting 'name'"},
            "test": {"type": "string", "description": "test source; defaults to same extension as script"},
            "test_name": {"type": "string", "description": "optional test_<stem> basename with test extension"},
            "test_runner": {"type": "array", "items": {"type": "string"}, "description": "optional test interpreter argv"},
            "area": {"type": "string", "description": "existing area from mm_list (case-insensitive)"},
            "usage": {"type": "string", "description": "index line: '<name> <args> — what it does [pure|read|write|irreversible]'"},
            "new_area": {"type": "boolean", "default": False, "description": "true only to create a new, clearly named area, not 'Misc'"},
            "runner": {"type": "array", "items": {"type": "string"},
                       "description": "override the default runner: argv, e.g. ['bash']; must already be installed"},
            "scope": {"type": "string", "enum": ["personal", "project"], "default": "personal",
                      "description": "'project' for scripts specific to this codebase; 'personal' otherwise"},
            "replace": {"type": "boolean", "default": False,
                        "description": "overwrite an existing script/test to repair or update it; restored if the new test fails"}},
            "required": ["name", "code", "test", "area", "usage"]},
    },
    {
        "name": "mm_setup",
        "description": (
            "Set up a library: the personal one (mode: local, existing or clone) or this project's "
            "(shared: true via the project's own git, false to keep it private to this user). Call "
            "only after asking the user which they want — mm_list, mm_run and mm_save say when setup "
            "is needed."),
        "inputSchema": {"type": "object", "properties": {
            "target": {"type": "string", "enum": ["personal", "project"], "default": "personal"},
            "mode": {"type": "string", "enum": ["local", "existing", "clone"]},
            "path": {"type": "string"}, "remote": {"type": "string"},
            "git": {"type": "boolean", "default": False}, "shared": {"type": "boolean"}}},
    },
]


def call(name, args):
    if name == "mm_setup":
        target = args.get("target", "personal")
        if target not in ("personal", "project"):
            raise ValueError(f"target must be 'personal' or 'project', got {target!r}")
        if target == "project":
            return onboarding.setup_project(args.get("shared")), False
        mode = args.get("mode")
        if mode not in ("local", "existing", "clone"):
            raise ValueError(f"mode must be 'local', 'existing' or 'clone', got {mode!r}")
        return onboarding.setup_personal(mode, args.get("path"),
                                        args.get("remote"), bool(args.get("git", False))), False
    if name in ("mm_list", "mm_run", "mm_save") and not paths.personal_root().is_dir():
        return onboarding.NOT_SET_UP, False
    if name == "mm_list":
        if not (args.get("area") or "").strip():
            return core.list_areas(), False
        return core.list_area(args["area"]), False
    if name == "mm_run":
        code, out, err = core.run(args["script"], [str(a) for a in args.get("args", [])])
        text = out + (f"\n[stderr]\n{err}" if err.strip() else "") + (f"\n[exit {code}]" if code else "")
        return text.strip() or f"[exit {code}, no output]", code != 0
    if name == "mm_save":
        ok, msg = core.save(args["name"], args["code"], args["test"], args["area"], args["usage"],
                          scope=args.get("scope", "personal"),
                          new_area=bool(args.get("new_area", False)), runner=args.get("runner"),
                          test_name=args.get("test_name"), test_runner=args.get("test_runner"),
                          replace=bool(args.get("replace", False)))
        return msg, not ok
    raise ValueError(f"unknown tool {name}")


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notification
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "muscle-memory", "version": __version__},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params", {})
        try:
            text, is_err = call(p.get("name"), p.get("arguments") or {})
        except Exception as e:  # report tool errors as results, not protocol errors
            text, is_err = f"error: {e}", True
        result = {"content": [{"type": "text", "text": text}], "isError": is_err}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
