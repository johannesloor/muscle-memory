"""Save a script (+ test, + optional runner sidecar) into a library.

Handles everything core.save() used to do, plus what it didn't: a `scope` ("personal" or
"project"), `replace` (overwrite an existing script after the same test gate, restoring the old
bytes on failure), a cross-library clash refusal (the same name in the *other* library is
refused, never silently shadowed), and git behaviour that follows the target folder, not a mode
flag:

- personal library: not a git repo -> files only; a git repo with no remote -> commit; a repo
  with a remote -> commit and push (a failed push keeps the commit).
- project library: never commits or pushes (that would mix into the user's own branch); it
  reports which paths changed so the user commits them, and -- once -- warns if the folder is now
  inside git but was never marked shared or private (see mm_setup, Task 6).

Git commands always run through runners.run_process(["git", "-C", str(root), ...]) with
GIT_TERMINAL_PROMPT=0, never a shell; a git failure is reported in the message, never raised.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import paths, runners, testing
from .index import add_index_line, parse_index
from .runners import validate_runner_argv

NAME_RE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9]{0,9}")

# Never prompts for credentials (ruling f): a push to a bogus/unreachable remote fails fast
# instead of hanging on a terminal prompt.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0"}


def _read_index_text(root):
    """root's INDEX.md as UTF-8, newline='' preserved (empty string if it doesn't exist)."""
    p = Path(root) / "INDEX.md"
    if not p.exists():
        return ""
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def _areas(text):
    counts = {}
    for e in parse_index(text):
        counts[e["area"]] = counts.get(e["area"], 0) + 1
    return list(counts.items())


def _other_library(scope):
    """(root, scope) of the other, currently-set-up library; (None, None) if there isn't one."""
    if scope == "personal":
        proj = paths.project_root()
        if proj is not None:
            proj_lib = (proj / ".muscle-memory").resolve()
            if (proj_lib / "INDEX.md").is_file():
                return proj_lib, "project"
        return None, None
    personal = paths.personal_root()
    if personal.is_dir():
        return personal, "personal"
    return None, None


def _resolve_root(scope):
    """Target library root for scope ('personal' or 'project'); raises ValueError per the spec."""
    if scope == "personal":
        return paths.personal_root()
    if scope != "project":
        raise ValueError(f"unknown scope '{scope}'; use 'personal' or 'project'")
    proj = paths.project_root()
    if proj is None:
        raise ValueError("no project here; save with scope 'personal'")
    lib_root = (proj / ".muscle-memory").resolve()
    if not (lib_root / "INDEX.md").is_file():
        if _in_git_worktree(proj):
            raise ValueError(
                "this project has no library yet: ask the user whether it should be shared "
                "through the project's git or kept private, then call "
                "mm_setup(target='project', shared=\u2026)")
        raise ValueError(
            "this project has no library yet: call mm_setup(target='project') to create it.")
    return lib_root


def _run_git(root, *args, timeout=30):
    """runners.run_process(["git", "-C", root, *args]); (1, "", str(e)) instead of raising."""
    try:
        return runners.run_process(["git", "-C", str(root), *args], cwd=root, env=GIT_ENV, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def _in_git_worktree(root):
    if not shutil.which("git"):
        return False
    code, out, _ = _run_git(root, "rev-parse", "--is-inside-work-tree", timeout=10)
    return code == 0 and out.strip() == "true"


def _git_dir(root):
    """The git dir for root (resolved to an absolute path), handling worktrees; None if not git."""
    if not shutil.which("git"):
        return None
    code, out, _ = _run_git(root, "rev-parse", "--git-dir", timeout=10)
    if code != 0 or not out.strip():
        return None
    gd = Path(out.strip())
    return gd if gd.is_absolute() else (Path(root) / gd).resolve()


def _project_git_warning(proj_root, lib_root):
    """The once-per-save 'ask the user' note (ruling d), or None if it doesn't apply."""
    git_dir = _git_dir(proj_root)
    if git_dir is None:
        return None
    exclude_file = git_dir / "info" / "exclude"
    excluded = False
    if exclude_file.is_file():
        lines = [l.strip() for l in exclude_file.read_text(encoding="utf-8", errors="replace").splitlines()]
        excluded = ".muscle-memory/" in lines or ".muscle-memory" in lines
    shared = (lib_root / ".shared").exists()
    if excluded or shared:
        return None
    return ("note: this project library is now inside git but was never set up as shared or "
            "private; ask the user (mm_setup)")


def _commit_message(verb, name, usage):
    summary = usage[len(name):].split("\u2014")[-1].strip()[:60]
    return f"{verb} {name}: {summary}" if summary else f"{verb} {name}"


def _save_personal_git(root, name, usage, add_paths, is_update, push):
    """Commit (and maybe push) for the personal library; never raises."""
    if not shutil.which("git"):
        return "saved, tests pass; not committed: git not found"
    code, _, _ = _run_git(root, "rev-parse", "--git-dir", timeout=10)
    if code != 0:
        return "saved, tests pass (not under git)"
    code, _, err = _run_git(root, "add", *add_paths)
    if code != 0:
        return "saved, tests pass; not committed: git add failed: " + err.strip()[-300:]
    message = _commit_message("Update" if is_update else "Add", name, usage)
    code, _, err = _run_git(root, "commit", "--only", "-m", message, "--", *add_paths)
    if code != 0:
        return "saved, tests pass; commit failed: " + err.strip()[-300:]
    msg = "saved, tests pass, committed"
    _, remote_out, _ = _run_git(root, "remote", timeout=10)
    if push and remote_out.strip():
        pcode, _, perr = _run_git(root, "push", timeout=60)
        msg += ", pushed" if pcode == 0 else "; push failed: " + perr.strip()[-300:]
    return msg


def _replace_index_entry(text, name, new_area, new_line):
    """Swap the old index line for name with new_line: in place if the area is unchanged, moved
    to new_area's section otherwise. The old line is found by script name at line start
    (ruling b), via index.parse_index.
    """
    entries = parse_index(text)
    old = next(e for e in entries if e["script"] == name)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    if old["area"] == new_area:
        for i, l in enumerate(lines):
            if l.strip() == old["line"]:
                lines[i] = new_line
                break
        result = "\n".join(lines) + "\n"
    else:
        lines = [l for l in lines if l.strip() != old["line"]]
        result = add_index_line("\n".join(lines) + "\n", new_area, new_line)
        result = result.replace("\r\n", "\n")  # add_index_line saw no \r\n in our '\n'-only input
    return result.replace("\n", newline) if newline != "\n" else result


def _guard_targets(root, targets):
    """Reject symlinked write paths and parent components escaping the resolved library root."""
    root = Path(root).resolve()
    for target in targets:
        target = Path(target)
        cur = target
        while cur != root and cur != cur.parent:
            if cur.is_symlink():
                raise ValueError(f"refusing symlink mutation target: {cur}")
            cur = cur.parent
        if cur != root:
            raise ValueError(f"mutation target escapes library root: {target}")
        if target.parent.resolve() != target.parent:
            if not target.parent.resolve().is_relative_to(root):
                raise ValueError(f"mutation target escapes library root: {target}")


def save(name, code, test, area, usage, *, scope="personal", new_area=False, runner=None,
          test_name=None, test_runner=None, replace=False, push=True):
    """Save name (+ test, + optional runner sidecar) into scope's library.

    Returns (ok, message). Raises ValueError for a bad name/area/runner, an unresolvable project
    scope, or a clash in the *other* library. A failing test gate returns (False, ...); with
    replace=True the previous script/test/sidecar bytes are restored exactly first.
    """
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must look like snake_case.ext (lowercase letters/digits/underscore, "
                          "then a lowercase extension, e.g. greet.py, greet.sh, greet.swift)")
    root = _resolve_root(scope)
    suffix = Path(name).suffix.lower()
    if runner is not None:
        runner = validate_runner_argv(runner)
    elif suffix not in runners.BUILTIN_EXTENSIONS:
        raise ValueError(f"no built-in runner for '{suffix}' scripts; pass runner=[...] "
                          f"(argv for the interpreter, e.g. ['deno', 'run']) to save it")
    if test_runner is not None:
        test_runner = validate_runner_argv(test_runner)
    if test_name is not None:
        if (not isinstance(test_name, str) or Path(test_name).name != test_name
                or not re.fullmatch(rf"test_{re.escape(Path(name).stem)}\.[a-z][a-z0-9]{{0,9}}", test_name)):
            raise ValueError("test_name must be a safe test_<script-stem> basename with a lowercase extension")
    effective_test_name = test_name or Path(testing.test_path(Path('.'), name)).name

    other_root, other_scope = _other_library(scope)
    if other_root is not None and (other_root / "scripts" / name).exists():
        raise ValueError(f"{name} already exists in the {other_scope} library; use "
                          f"scope='{other_scope}' and replace=True there instead, or pick a "
                          "different name.")

    area = area.strip()
    index_text = _read_index_text(root)
    known = [a for a, _ in _areas(index_text)]
    existing_area = next((a for a in known if a.lower() == area.lower()), None)
    if existing_area:
        area = existing_area  # reuse the canonical spelling
    elif not new_area:
        raise ValueError(f"no area '{area}'. Existing areas: {', '.join(known) or '(none)'}. "
                          "Use one of them, or pass new_area to create it.")
    elif not area or area.startswith("#"):
        raise ValueError("area name must be non-empty text")

    script = root / "scripts" / name
    test_file = root / "tests" / effective_test_name
    default_test_file = testing.test_path(root, name)
    test_meta_path = testing.test_metadata_path(root, name)
    current_meta = None
    if test_meta_path.exists():
        try:
            current_meta = json.loads(test_meta_path.read_text(encoding="utf-8")).get("name")
        except (ValueError, AttributeError):
            current_meta = None
    owned_test = test_file == default_test_file or (replace and current_meta == effective_test_name)
    if test_file.exists() and (not replace or not owned_test):
        raise ValueError(f"test file {effective_test_name} already exists; refusing to overwrite another script's test")
    runner_path = root / "scripts" / f"{name}.runner.json"
    index_path = root / "INDEX.md"
    _guard_targets(root, [script, test_file, test_meta_path, runner_path, index_path, default_test_file])
    exists = script.exists()
    if exists and not replace:
        raise ValueError(f"{name} already exists; call save with replace=True to update it")
    if not exists and replace:
        raise ValueError(f"{name} does not exist yet; call save without replace to create it")

    if not usage.startswith(name):
        usage = f"{name} {usage}"

    backup = None
    if replace:
        backup = {
            "script": script.read_bytes() if script.exists() else None,
            "test": test_file.read_bytes() if test_file.exists() else None,
            "test_meta": test_meta_path.read_bytes() if test_meta_path.exists() else None,
            "old_test_path": default_test_file if default_test_file != test_file else None,
            "old_test": default_test_file.read_bytes() if default_test_file != test_file and default_test_file.exists() else None,
            "runner": runner_path.read_bytes() if runner_path.exists() else None,
        }

    script.parent.mkdir(parents=True, exist_ok=True)
    test_file.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(code, encoding="utf-8")
    test_file.write_text(test, encoding="utf-8")
    if test_name is not None or test_runner is not None:
        payload = {"name": effective_test_name}
        if test_runner is not None:
            payload["runner"] = test_runner
        test_meta_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    else:
        test_meta_path.unlink(missing_ok=True)
    if runner is not None:
        runner_path.write_text(json.dumps({"argv": runner}) + "\n", encoding="utf-8")

    try:
        ok, test_msg = testing.run_test(root, name)
    except Exception:
        if backup is not None:
            restore = [(script, backup["script"]), (test_file, backup["test"]),
                       (test_meta_path, backup["test_meta"]), (runner_path, backup["runner"])]
            if backup["old_test_path"] is not None:
                restore.append((backup["old_test_path"], backup["old_test"]))
            for path, old in restore:
                if old is not None:
                    path.write_bytes(old)
                else:
                    path.unlink(missing_ok=True)
        else:
            script.unlink(missing_ok=True)
            test_file.unlink(missing_ok=True)
            test_meta_path.unlink(missing_ok=True)
            runner_path.unlink(missing_ok=True)
        raise
    if not ok:
        if backup is not None:
            restore = [(script, backup["script"]), (test_file, backup["test"]),
                       (test_meta_path, backup["test_meta"]), (runner_path, backup["runner"])]
            if backup["old_test_path"] is not None:
                restore.append((backup["old_test_path"], backup["old_test"]))
            for path, old in restore:
                if old is not None:
                    path.write_bytes(old)
                else:
                    path.unlink(missing_ok=True)
            return False, ("test failed; the previous version was restored. Fix the code or the "
                            "test and call save again.\n" + test_msg)
        script.unlink(missing_ok=True)
        test_file.unlink(missing_ok=True)
        test_meta_path.unlink(missing_ok=True)
        runner_path.unlink(missing_ok=True)
        return False, "test failed; nothing saved. Fix the code or the test and call save again.\n" + test_msg

    if replace:
        if backup["old_test_path"] is not None and backup["old_test"] is not None:
            backup["old_test_path"].unlink(missing_ok=True)
        new_text = _replace_index_entry(index_text, name, area, usage)
    else:
        new_text = add_index_line(index_text, area, usage)
    with open(index_path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)

    add_paths = [f"scripts/{name}", test_file.relative_to(root).as_posix(), "INDEX.md"]
    if runner_path.exists():
        add_paths.append(f"scripts/{name}.runner.json")
    if test_meta_path.exists():
        add_paths.append(f"scripts/{name}.test.json")

    if scope == "personal":
        msg = _save_personal_git(root, name, usage, add_paths, is_update=replace, push=push)
    else:
        msg = ("saved, tests pass; not committed \u2014 commit these with your work: "
               + ", ".join(add_paths))
        warning = _project_git_warning(root.parent, root)
        if warning:
            msg += "\n" + warning
    return True, msg
