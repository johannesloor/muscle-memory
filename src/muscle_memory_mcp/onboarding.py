"""First-run creation and adoption of personal and project libraries."""
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from .paths import personal_root, project_root

NOT_SET_UP = (
    "The personal script library isn't set up yet. Ask the user once where it should live: "
    "(1) local folder only (2) an existing folder or git repo — ask for the path "
    "(3) clone a git remote — ask for the URL; then call mm_setup."
)

GIT_NOT_FOUND = "git not found"


def _layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    index = root / "INDEX.md"
    if not index.exists():
        with open(index, "w", encoding="utf-8", newline="") as f:
            f.write("# Muscle memory index\n")
    (root / "scripts").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)


def setup_personal(mode: Literal["local", "existing", "clone"], path: str | None = None,
                   remote: str | None = None, git: bool = False) -> str:
    default = personal_root()
    root = Path(path).expanduser().resolve() if path is not None else default
    if mode == "clone":
        if not remote:
            raise ValueError("clone requires a remote URL")
        if root.exists() and any(root.iterdir()):
            raise ValueError(f"target folder is not empty: {root}")
        if not shutil.which("git"):
            raise ValueError(GIT_NOT_FOUND)
        root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", remote, str(root)], check=True, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
        _layout(root)
        result = f"Cloned personal library to {root}."
    elif mode in ("local", "existing"):
        _layout(root)
        if mode == "local" and git and not (root / ".git").exists():
            if not shutil.which("git"):
                raise ValueError(GIT_NOT_FOUND)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        result = f"Personal library ready at {root}."
    else:
        raise ValueError("mode must be 'local', 'existing' or 'clone'")
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
