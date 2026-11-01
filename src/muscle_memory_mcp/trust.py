"""Explicit, per-project trust for project-library execution.

The user approves a project library once with `muscle-memory-mcp trust --project`. The
record lives outside the project, keyed by the library's resolved path, and covers later
changes to that library. A file inside the project can never mark itself trusted.
"""
import json
from pathlib import Path


def _state_path():
    return Path.home() / ".local" / "share" / "muscle-memory-mcp" / "trusted-projects.json"


def _state():
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def approved(root):
    return _state().get(str(Path(root).resolve())) is True


def approve(root):
    root = Path(root).resolve()
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _state()
    state[str(root)] = True
    path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")


def require(root):
    if not approved(root):
        root = Path(root).resolve()
        raise ValueError("project library is not trusted; ask the user to review it and run "
                         f"`muscle-memory-mcp trust --project` in the project (path: {root})")
