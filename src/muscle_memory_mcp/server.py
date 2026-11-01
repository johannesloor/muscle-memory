"""Muscle-memory MCP server (stdio, JSON-RPC 2.0, standard library only).

Exposes the library as tools so every harness that speaks MCP sees them on every step:
  mm_list, mm_run, mm_save.  Register as:  python -m muscle_memory_mcp
"""
import json
import sys

from . import __version__, core, onboarding, paths

# MCP revisions this server speaks (tools only, stdio), newest first; initialize answers with the
# client's requested revision when listed, otherwise the newest.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = "For reusable substeps, call mm_list before writing code; project matches win. Run matches; no match, use language that fits. Ask the user before mm_setup. Save reusable work after results. Project libraries run only after the user runs CLI trust --project; never run it for them."


TOOLS = [
    {
        "name": "mm_list",
        "description": (
            "List tested scripts; call before code at each reusable substep. Omit area to list areas, "
            "then select one. Same-name project scripts shadow personal. No matching script: write code. If "
            "unavailable, ask once before mm_setup."),
        "inputSchema": {"type": "object", "properties": {
            "area": {"type": "string", "description": "Area from first call; omit to list areas."}}},
    },
    {
        "name": "mm_run",
        "description": (
            "Run code as you with inherited environment; no sandbox. Report output. [write]: --dry-run then readback. "
            "[irreversible]: --dry-run, show output, wait for explicit yes before execution. Repair via mm_save replace: true."),
        "inputSchema": {"type": "object", "properties": {
            "script": {"type": "string", "description": "Script file name."},
            "args": {"type": "array", "items": {"type": "string"}, "default": []}}, "required": ["script"]},
    },
    {
        "name": "mm_save",
        "description": (
            "Save reusable code and EXECUTE its test, unsandboxed; tests are not security reviews. "
            "Untrusted project library: nothing written or run; ask the user. "
            "Use args, env secrets, --dry-run for actions. Tests default to script runner/extension; "
            "test_name/test_runner override. MM_SCRIPT/MM_LIBRARY set; exit 0 passes. "
            "Failed new personal test saves nothing; replacement restores prior files. "
            "Personal commits, and pushes if chosen at setup; project never commits. Existing names: replace: true."),
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "snake_case file name with language extension."},
            "code": {"type": "string", "description": "Script source."},
            "test": {"type": "string", "description": "Test source."},
            "test_name": {"type": "string", "description": "test_<script-stem>.<ext> basename."},
            "test_runner": {"type": "array", "items": {"type": "string"}, "description": "Test interpreter argv."},
            "area": {"type": "string", "description": "Existing mm_list area."},
            "usage": {"type": "string", "description": "Usage line: <name> <args> — purpose [pure|read|write|irreversible]."},
            "new_area": {"type": "boolean", "default": False, "description": "Create a new area."},
            "runner": {"type": "array", "items": {"type": "string"},
                       "description": "Installed runner argv."},
            "scope": {"type": "string", "enum": ["personal", "project"], "default": "personal",
                      "description": "Codebase-specific or personal."},
            "replace": {"type": "boolean", "default": False,
                        "description": "Replace; restore if test fails."}},
            "required": ["name", "code", "test", "area", "usage"]},
    },
    {
        "name": "mm_setup",
        "description": (
            "After asking which: personal (mode local/existing/clone) or project "
            "(shared true uses project git, false keeps private). push: user's answer to "
            "'Push scripts to git remote automatically?'"),
        "inputSchema": {"type": "object", "properties": {
            "target": {"type": "string", "enum": ["personal", "project"], "default": "personal"},
            "mode": {"type": "string", "enum": ["local", "existing", "clone"]},
            "path": {"type": "string"}, "remote": {"type": "string"},
            "git": {"type": "boolean", "default": False}, "shared": {"type": "boolean"},
            "push": {"type": "boolean"}}},
    },
]


REQUIRED = {"mm_run": ("script",), "mm_save": ("name", "code", "test", "area", "usage")}


def call(name, args):
    for field in REQUIRED.get(name, ()):
        if field not in args:
            raise ValueError(f"missing required argument '{field}'")
    if name == "mm_setup":
        target = args.get("target", "personal")
        if target not in ("personal", "project"):
            raise ValueError(f"target must be 'personal' or 'project', got {target!r}")
        if target == "project":
            return onboarding.setup_project(args.get("shared")), False
        push = args.get("push")
        if push is not None and not isinstance(push, bool):
            raise ValueError("push must be a boolean")
        mode = args.get("mode")
        if mode not in ("local", "existing", "clone"):
            raise ValueError(f"mode must be 'local', 'existing' or 'clone', got {mode!r}")
        return onboarding.setup_personal(mode, args.get("path"),
                                        args.get("remote"), bool(args.get("git", False)), push), False
    if name in ("mm_list", "mm_run", "mm_save") and not paths.personal_root().is_dir():
        return onboarding.NOT_SET_UP, False
    if name == "mm_list":
        if not (args.get("area") or "").strip():
            return core.list_areas(), False
        return core.list_area(args["area"]), False
    if name == "mm_run":
        run_args = args.get("args", [])
        if not isinstance(run_args, list) or not all(isinstance(a, (str, int, float)) for a in run_args):
            raise ValueError("args must be a list of strings")
        code, out, err = core.run(args["script"], [str(a) for a in run_args])
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


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_line(msg):
    """Reply for one decoded stdin line: a single request or a JSON-RPC batch (list)."""
    if isinstance(msg, list):
        if not msg:
            return _error(None, -32600, "invalid request: empty batch")
        replies = [r for r in (handle(m) for m in msg) if r is not None]
        return replies or None  # a batch of only notifications gets no reply
    return handle(msg)


def handle(msg):
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request: expected a JSON object")
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notification
    if msg.get("jsonrpc") != "2.0":
        return _error(mid, -32600, "invalid request: jsonrpc must be '2.0'")
    if not isinstance(method, str):
        return _error(mid, -32600, "invalid request: method must be a string")
    params = msg.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(mid, -32602, "invalid params: params must be an object")
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        result = {"protocolVersion": version,
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "muscle-memory", "version": __version__},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(mid, -32602, "invalid params: arguments must be an object")
        try:
            text, is_err = call(params.get("name"), args)
        except Exception as e:  # report tool errors as results, not protocol errors
            text, is_err = f"error: {e}", True
        result = {"content": [{"type": "text", "text": text}], "isError": is_err}
    else:
        return _error(mid, -32601, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    # MCP stdio is UTF-8 by spec; don't inherit a legacy locale code page (Windows, piped stdio).
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = _error(None, -32700, "parse error")
        else:
            try:
                reply = handle_line(msg)
            except Exception as e:  # one bad message must never end the session
                reply = _error(msg.get("id") if isinstance(msg, dict) else None, -32603, f"internal error: {e}")
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
