"""Run a saved script's test; tests choose their own language and runner.

Tests receive MM_SCRIPT and MM_LIBRARY in their environment. Exit code 0 passes.
"""
import json
from pathlib import Path
import sys

from . import runners


def test_path(lib_root: Path, name: str) -> Path:
    stem, suffix = Path(name).stem, Path(name).suffix
    return Path(lib_root) / "tests" / f"test_{stem}{suffix}"


def test_metadata_path(lib_root: Path, name: str) -> Path:
    return Path(lib_root) / "scripts" / f"{name}.test.json"


def run_test(lib_root: Path, name: str, timeout: int = 600) -> tuple[bool, str]:
    lib_root = Path(lib_root).resolve()
    script = (lib_root / "scripts" / name).resolve()
    metadata = test_metadata_path(lib_root, name)
    meta = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else None
    if meta:
        test_name = meta.get("name")
        if (not isinstance(test_name, str) or Path(test_name).name != test_name
                or not test_name.startswith(f"test_{script.stem}.") or test_name.endswith(".")):
            raise ValueError(f"invalid test metadata for {name}")
    t_path = lib_root / "tests" / meta["name"] if meta else test_path(lib_root, name)
    if not t_path.exists() and not meta and script.suffix.lower() != ".py":
        legacy_path = lib_root / "tests" / f"test_{script.stem}.py"
        if legacy_path.exists():
            t_path, argv = legacy_path, [sys.executable]
        else:
            raise ValueError(f"no test file found for {name}: expected {test_path(lib_root, name)} or {legacy_path}")
    else:
        if meta and "runner" in meta:
            argv = runners.validate_runner_argv(meta["runner"])
            argv = [runners._resolve_executable(argv[0], t_path.name), *argv[1:]]
        elif meta:
            argv = runners._builtin_argv_prefix(t_path.suffix.lower())
            argv = [runners._resolve_executable(argv[0], t_path.name), *argv[1:]]
        else:
            argv = runners.runner_for(script)
    cmd = [*argv, str(t_path)]
    env = {"MM_SCRIPT": str(script), "MM_LIBRARY": str(lib_root)}
    try:
        code, out, err = runners.run_process(cmd, cwd=lib_root, env=env, timeout=timeout)
    except FileNotFoundError as e:
        raise ValueError(f"failed to launch '{argv[0]}' for {name}: {e}")
    return code == 0, (out + err)[-3000:]
