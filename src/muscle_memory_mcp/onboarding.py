"""First-run creation and adoption of personal and project libraries."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from .paths import personal_root, project_root

NOT_SET_UP = (
    "The personal script library isn't set up yet. Ask the user once where it should live: "
    "(1) local folder only (2) an existing folder or git repo — ask for the path "
    "(3) clone a git remote — ask for the URL. With git, also ask: Push scripts to git remote "
    "automatically? Then call mm_setup."
)

GIT_NOT_FOUND = "git not found"
# Transports git may use for any library operation; blocks helper transports such as ext::
# (arbitrary command execution) even where a repo or user config allows them.
GIT_ALLOW_PROTOCOL = "file:git:http:https:ssh"
CLONE_TIMEOUT = 120  # the server is single-threaded; a stalled clone must not block it forever


def _undo_clone(root: Path, existed: bool) -> None:
    """Remove a newly created failed clone, but never delete from a user-owned directory."""
    if not existed:
        shutil.rmtree(root, ignore_errors=True)


def _git_env() -> dict:
    """Environment for git calls: no interactive prompts, safe transports only."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": GIT_ALLOW_PROTOCOL}


def _layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    index = root / "INDEX.md"
    if not index.exists():
        with open(index, "w", encoding="utf-8", newline="") as f:
            f.write("# Muscle memory index\n")
    (root / "scripts").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)


PUSH_KEY = "muscle-memory.push"


def _set_push(root: Path, push: bool) -> str:
    """Store the user's push choice in the library's own git config (never committed or cloned)."""
    if not (root / ".git").exists() or not shutil.which("git"):
        return " Not a git repo, so there is nothing to push."
    try:
        subprocess.run(["git", "-C", str(root), "config", "--local", PUSH_KEY, "true" if push else "false"],
                       check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=_git_env(), timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError(f"could not save the push setting: {e}")
    return (" Scripts will be pushed to the git remote automatically." if push
            else " Scripts will not be pushed automatically.")


def setup_personal(mode: Literal["local", "existing", "clone"], path: str | None = None,
                   remote: str | None = None, git: bool = False, push: bool | None = None) -> str:
    default = personal_root()
    root = Path(path).expanduser().resolve() if path is not None else default
    if mode == "clone":
        if not remote:
            raise ValueError("clone requires a remote URL")
        if remote.strip().startswith("-"):
            raise ValueError("remote must not start with '-'; pass a git URL or path")
        if root.exists() and any(root.iterdir()):
            raise ValueError(f"target folder is not empty: {root}")
        if not shutil.which("git"):
            raise ValueError(GIT_NOT_FOUND)
        root.parent.mkdir(parents=True, exist_ok=True)
        existed = root.exists()
        cleanup_note = f" Existing target {root} was preserved; inspect and clear partial clone files before retrying." if existed else ""
        # "--" ends option parsing so remote can never be read as a git option.
        try:
            subprocess.run(["git", "clone", "--", remote, str(root)], check=True, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=_git_env(),
                           timeout=CLONE_TIMEOUT)
        except subprocess.TimeoutExpired:
            _undo_clone(root, existed)
            raise ValueError(f"git clone timed out after {CLONE_TIMEOUT}s; check the URL, network "
                             "and that the remote needs no interactive login" + cleanup_note)
        except subprocess.CalledProcessError as e:
            _undo_clone(root, existed)
            raise ValueError("git clone failed: " + (e.stderr or "").strip()[-300:] + cleanup_note)
        _layout(root)
        result = f"Cloned personal library to {root}."
    elif mode in ("local", "existing"):
        _layout(root)
        if mode == "local" and git and not (root / ".git").exists():
            if not shutil.which("git"):
                raise ValueError(GIT_NOT_FOUND)
            try:
                subprocess.run(["git", "init", str(root)], check=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=_git_env(), timeout=30)
            except subprocess.TimeoutExpired:
                raise ValueError("git init timed out after 30s")
            except subprocess.CalledProcessError as e:
                raise ValueError("git init failed: " + (e.stderr or "").strip()[-300:])
        result = f"Personal library ready at {root}."
    else:
        raise ValueError("mode must be 'local', 'existing' or 'clone'")
    if push is not None:
        result += _set_push(root, push)
    if root != default:
        result += (f" Add MUSCLE_MEMORY_DIR={root} to this server's env in your MCP config, "
                   "then reconnect.")
    return result


def setup_project(shared: bool | None = None) -> str:
    """Create/adopt the project library at <project_root>/.muscle-memory.

    When the project is inside git and `shared` isn't given, nothing is created: the caller
    must ask the user whether the library should be shared through the project's git or kept
    private, then call mm_setup(target='project', shared=True|False) (controller ruling,
    Task 6 review). Outside git, `shared` is ignored as before.
    """
    project = project_root()
    if project is None:
        raise ValueError("no project here")
    root = project / ".muscle-memory"
    if not shutil.which("git"):
        _layout(root)
        return f"Project library ready at {root} ({GIT_NOT_FOUND})."
    try:
        out = subprocess.run(["git", "-C", str(project), "rev-parse", "--git-path", "info/exclude"],
                             check=True, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10).stdout.strip()
        exclude = Path(out)
        if not exclude.is_absolute():
            exclude = project / exclude
    except (OSError, subprocess.SubprocessError):
        _layout(root)
        return f"Project library ready at {root} (not under git)."
    if shared is None:
        raise ValueError(
            "this project is under git; ask the user whether the library should be shared "
            "through the project's git or kept private, then call "
            "mm_setup(target='project', shared=True|False)."
        )
    _layout(root)
    if shared is True:
        (root / ".shared").touch(exist_ok=True)
        return f"Project library ready at {root}; marked shared."
    if shared is False:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".muscle-memory/" not in [line.strip() for line in existing.splitlines()]:
            with open(exclude, "a", encoding="utf-8", newline="") as f:
                if existing and not existing.endswith(("\n", "\r")):
                    f.write("\n")
                f.write(".muscle-memory/\n")
        return f"Project library ready at {root}; kept private by excluding it from git."
    return f"Project library ready at {root}."
