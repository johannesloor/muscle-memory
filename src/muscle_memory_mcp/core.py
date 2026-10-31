"""Core library: list, run and save scripts in the muscle-memory library.

Personal library dir: $MUSCLE_MEMORY_DIR or ~/.muscle-memory; optional project library at the
project root's .muscle-memory/ (see paths.py). Both have scripts/ and tests/. This module is
standard library only. Scripts may be in any language: common ones are picked up by file
extension (see runners.LANGUAGE_RUNNERS); anything else needs an explicit --runner argv.
Each script's test runs in the script's own language, via testing.run_test() (see testing.py).

list_areas()/list_area()/resolve() see both the personal and (if present) project library,
labelled by scope, with the project library taking precedence on a name clash. areas()/
area_lines()/format_areas() are lower-level, single-library helpers. Save behaviour lives in store.py;
core.save() is retained as a compatibility re-export.
"""
import os
from pathlib import Path

from . import paths, runners
from .onboarding import NOT_SET_UP
from .index import add_index_line, parse_index  # noqa: F401 (re-exported; moved from here)

def lib_dir():
    return paths.personal_root()


def _read_index_text(root):
    """root's INDEX.md as UTF-8, newline='' preserved (empty string if it doesn't exist).

    Path.read_text() only gained a newline= parameter in Python 3.13; this module supports
    Python >=3.10, so open() is used directly instead.
    """
    p = Path(root) / "INDEX.md"
    if not p.exists():
        return ""
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def read_index():
    return _read_index_text(lib_dir())


def areas(text=None):
    """[(area, script count)] in index order, for a single library's index text."""
    counts = {}
    for e in parse_index(read_index() if text is None else text):
        counts[e["area"]] = counts.get(e["area"], 0) + 1
    return list(counts.items())


def area_lines(area, text=None):
    """All index lines in one area (case-insensitive name); ValueError lists the areas if unknown."""
    text = read_index() if text is None else text
    known = [a for a, _ in areas(text)]
    match = next((a for a in known if a.lower() == area.strip().lower()), None)
    if match is None:
        raise ValueError(f"no area '{area}'. Areas: {', '.join(known) or '(library is empty)'}")
    return match, [e["line"] for e in parse_index(text) if e["area"] == match]


def format_areas(pairs):
    if not pairs:
        return "The library is empty. Do the task, answer, then save a script if the step is reusable."
    return ("Areas (script count):\n" + "\n".join(f"- {a} ({n})" for a, n in pairs)
            + "\n\nList an area's scripts by calling again with that area. If no area fits, there is no script for this step.")


def list_areas():
    """Areas from every visible library (project first), each labelled with its scope."""
    if not paths.personal_root().is_dir():
        return NOT_SET_UP
    triples = []  # (area, count, scope)
    for lib in paths.libraries():
        for area, count in areas(_read_index_text(lib.root)):
            triples.append((area, count, lib.scope))
    if not triples:
        return "The library is empty. Do the task, answer, then save a script if the step is reusable."
    lines = "\n".join(f"- {a} ({n}, {s})" for a, n, s in triples)
    return ("Areas (script count):\n" + lines
            + "\n\nList an area's scripts by calling again with that area. If no area fits, there is no script for this step.")


def list_area(area):
    """One area's lines from every library that has it, each under its own '## Area (scope)'.

    A personal line whose script also exists in the project library's scripts/ (so mm_run
    would pick the project one, per resolve()'s precedence) is marked '(shadowed)'.
    """
    if not paths.personal_root().is_dir():
        return NOT_SET_UP
    libs = paths.libraries()
    project_lib = next((lib for lib in libs if lib.scope == "project"), None)
    project_scripts = set()
    if project_lib is not None and (project_lib.root / "scripts").is_dir():
        project_scripts = {p.name for p in (project_lib.root / "scripts").iterdir() if p.is_file()}

    sections, known = [], []
    for lib in libs:
        text = _read_index_text(lib.root)
        lib_known = [a for a, _ in areas(text)]
        known.extend(a for a in lib_known if a not in known)
        match = next((a for a in lib_known if a.lower() == area.strip().lower()), None)
        if match is None:
            continue
        lines = []
        for e in parse_index(text):
            if e["area"] != match:
                continue
            line = e["line"]
            if lib.scope == "personal" and e["script"] in project_scripts:
                line += " (shadowed)"
            lines.append(line)
        sections.append(f"## {match} ({lib.scope})\n" + "\n".join(lines))
    if not sections:
        raise ValueError(f"no area '{area}'. Areas: {', '.join(known) or '(library is empty)'}")
    return "\n\n".join(sections)


def resolve(script):
    """(Library, Path) for script, checked project-first then personal; same name => project wins."""
    for lib in paths.libraries():
        d = (lib.root / "scripts").resolve()
        p = (d / script).resolve()
        if (p.parent == d and p.is_file() and not p.name.startswith("test_")
                and not p.name.endswith(".runner.json") and not p.name.endswith(".test.json")):
            return lib, p
    raise ValueError(f"not a library script: {script}")


def run(script, args, timeout=300):
    if not paths.personal_root().is_dir():
        raise ValueError(NOT_SET_UP)
    lib, p = resolve(script)
    argv = runners.runner_for(p)
    cmd = [*argv, str(p), *args]
    try:
        return runners.run_process(cmd, cwd=Path(os.getcwd()), env=None, timeout=timeout)
    except FileNotFoundError as e:
        raise ValueError(f"failed to launch '{argv[0]}' for {p.name}: {e}")


def save(name, code, test, area, usage, *, scope="personal", new_area=False, runner=None,
         test_name=None, test_runner=None, replace=False, push=True):
    """Compatibility wrapper; new callers should use store.save.

    Returns NOT_SET_UP for any scope when the personal library folder is missing, matching
    mm_list/mm_run/mm_save's MCP behaviour (Task 6 review, finding 4): a project-scope save
    still needs the personal folder to exist, since that's what marks first-run setup done.
    """
    from .store import save as store_save
    if not paths.personal_root().is_dir():
        return False, NOT_SET_UP
    return store_save(name, code, test, area, usage, scope=scope, new_area=new_area,
                      runner=runner, test_name=test_name, test_runner=test_runner,
                      replace=replace, push=push)
