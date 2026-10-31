"""Personal and project library locations.

Personal library: MUSCLE_MEMORY_DIR, or ~/.muscle-memory (per-machine, always present once set up).
Project library: an optional .muscle-memory/ folder at the project root (the git top-level of the
current working directory, or the working directory itself if it isn't a git repo);
MUSCLE_MEMORY_PROJECT_DIR overrides the root. Both library roots are always absolute
(Path.resolve()), so anything derived from them (e.g. MM_SCRIPT/MM_LIBRARY for tests) is too.
"""
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional


@dataclass(frozen=True)
class Library:
    root: Path
    scope: Literal["personal", "project"]


def personal_root() -> Path:
    return Path(os.environ.get("MUSCLE_MEMORY_DIR") or Path.home() / ".muscle-memory").resolve()


def project_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """The project root, or None if there isn't one (Review Focus 1: never the personal library).

    MUSCLE_MEMORY_PROJECT_DIR wins if set; otherwise the git top-level of cwd; otherwise cwd
    itself. Returns None if that resolves to the home directory, or if its '.muscle-memory'
    subfolder would resolve to the personal library (so the personal library is never
    double-counted as a project library of its own home directory).
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    env = os.environ.get("MUSCLE_MEMORY_PROJECT_DIR")
    if env:
        root = Path(env)
    else:
        root = None
        try:
            out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                                  capture_output=True, encoding="utf-8", errors="replace", timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            out = None
        if out is not None and out.returncode == 0 and out.stdout.strip():
            root = Path(out.stdout.strip())
        if root is None:
            root = cwd
    root = root.resolve()
    if root == Path.home().resolve():
        return None
    if (root / ".muscle-memory").resolve() == personal_root():
        return None
    return root


def libraries() -> list[Library]:
    """Project library first (only if its INDEX.md exists), then personal (if its folder exists)."""
    libs = []
    proj = project_root()
    if proj is not None:
        proj_lib_root = (proj / ".muscle-memory").resolve()
        if (proj_lib_root / "INDEX.md").is_file():
            libs.append(Library(root=proj_lib_root, scope="project"))
    personal = personal_root()
    if personal.is_dir():
        libs.append(Library(root=personal, scope="personal"))
    return libs
