"""Script runners: pick the interpreter argv for a script and run it, UTF-8 everywhere.

Built-in runners are looked up by lowercase file extension (LANGUAGE_RUNNERS, plus the
special-cased .py/.ps1/.cmd/.bat below); a '<script>.runner.json' sidecar with an explicit
{"argv": [...]} always wins over the built-in guess. runner_for() returns the full argv
prefix with argv[0] already resolved to an absolute path. run_process() launches that argv
as a real subprocess (never a shell) and always decodes its output as UTF-8.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Built-in runners by lowercase file extension: argv prefix to run a script with
# (the resolved script path and then the user's args are appended). ".py" always
# maps to sys.executable, and ".ps1"/".cmd"/".bat" are resolved dynamically below,
# so none of those three are listed here.
LANGUAGE_RUNNERS = {
    ".sh": ["bash"],
    ".bash": ["bash"],
    ".zsh": ["zsh"],
    ".js": ["node"],
    ".mjs": ["node"],
    ".cjs": ["node"],
    ".rb": ["ruby"],
    ".pl": ["perl"],
    ".php": ["php"],
    ".swift": ["swift"],
    ".go": ["go", "run"],
    ".lua": ["lua"],
    ".r": ["Rscript"],
}

# Extensions that only ever run on Windows, via the cmd.exe shell launcher.
WINDOWS_ONLY_EXTENSIONS = {".cmd", ".bat"}

# Every extension save() should treat as having a built-in runner (no explicit
# runner=[...] required), including the dynamically-resolved ones above.
BUILTIN_EXTENSIONS = set(LANGUAGE_RUNNERS) | WINDOWS_ONLY_EXTENSIONS | {".py", ".ps1"}


def validate_runner_argv(argv):
    """Raise ValueError unless argv is a non-empty list of non-empty strings."""
    if not isinstance(argv, list) or not argv:
        raise ValueError("runner must be a non-empty list of strings (argv), e.g. ['bash'] or ['deno', 'run']")
    if not all(isinstance(a, str) and a.strip() for a in argv):
        raise ValueError("runner argv entries must all be non-empty strings")
    return list(argv)


def runner_metadata_path(script_path):
    return script_path.with_name(script_path.name + ".runner.json")


def resolve_executable(exe, script_name):
    """Resolve exe (a bare name or an absolute path) to an absolute, executable path."""
    if os.path.isabs(exe) and os.access(exe, os.X_OK):
        return exe
    resolved = shutil.which(exe)
    if not resolved:
        raise ValueError(f"runner '{exe}' not found on PATH; install it to run {script_name}")
    return resolved


def builtin_argv_prefix(suffix):
    """Unresolved argv prefix (argv[0] may still be a bare name) for a built-in suffix."""
    if suffix == ".py":
        return [sys.executable]
    if suffix == ".ps1":
        if shutil.which("pwsh"):
            return ["pwsh", "-NoProfile", "-File"]
        if sys.platform == "win32" and shutil.which("powershell"):
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        raise ValueError("no built-in runner for '.ps1' scripts; re-save with an explicit runner argv")
    if suffix in WINDOWS_ONLY_EXTENSIONS:
        if sys.platform != "win32":
            raise ValueError(f"'{suffix}' scripts only run on Windows")
        return ["cmd", "/c"]
    argv = LANGUAGE_RUNNERS.get(suffix)
    if argv is None:
        raise ValueError(f"no built-in runner for '{suffix}' scripts; re-save with an explicit runner argv")
    return list(argv)


# cmd.exe re-parses a batch file's command line itself, and Python's list2cmdline quoting does
# not escape for it (the "BatBadBut" class), so these characters in an argument could start a
# second command. Reject them rather than attempt cmd.exe escaping.
CMD_METACHARACTERS = set('&|<>^%!"\r\n')


def check_cmd_args(script: Path, argv: list[str], args) -> None:
    """Raise ValueError if script runs through cmd.exe and an argument holds a cmd metacharacter."""
    via_cmd = (Path(script).suffix.lower() in WINDOWS_ONLY_EXTENSIONS
               or Path(argv[0]).name.lower() in ("cmd", "cmd.exe"))
    if not via_cmd:
        return
    for a in args:
        bad = sorted(CMD_METACHARACTERS & set(a))
        if bad:
            shown = " ".join(repr(c) for c in bad)
            raise ValueError(f"argument {a!r} contains {shown}, which cmd.exe would interpret; "
                             "batch scripts only accept arguments without & | < > ^ % ! \" or newlines")


def runner_for(script: Path) -> list[str]:
    """Full argv prefix (argv[0] resolved to an absolute path) to run script.

    The '<script>.runner.json' sidecar wins if present; otherwise the built-in
    runner for script's extension is used. Raises ValueError for an unknown
    extension or a runner missing from PATH.
    """
    meta = runner_metadata_path(script)
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid runner metadata for {script.name}: {e}")
        if not isinstance(data, dict) or "argv" not in data:
            raise ValueError(f"runner metadata for {script.name} must be a JSON object with an 'argv' list")
        argv = validate_runner_argv(data["argv"])
    else:
        argv = builtin_argv_prefix(script.suffix.lower())
    resolved = resolve_executable(argv[0], script.name)
    return [resolved, *argv[1:]]


def run_process(argv: list[str], cwd: Path, env: dict | None, timeout: int) -> tuple[int, str, str]:
    """Run argv (never a shell) in cwd; stdout/stderr are UTF-8 text with errors='replace'.

    env is merged over the current process environment (env=None means plain inherit);
    PYTHONIOENCODING=utf-8 is always set in the child so non-ASCII output round-trips
    correctly even on platforms whose default console/locale encoding is not UTF-8.
    """
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(argv, cwd=str(cwd), env=child_env, capture_output=True,
                        encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr
