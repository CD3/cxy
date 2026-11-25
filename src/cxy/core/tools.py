"""Find and run external tools."""

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from cxy.core.errors import CxyError

# Tools installed alongside cxy. Searched last, so the user's own tools win: switching
# between Conan versions would migrate the shared Conan cache back and forth.
_BUNDLED = str(Path(sys.executable).parent)

# Python packages named differently from the tool they provide.
_PACKAGES = {"cmake-lint": "cmakelang"}


def search_path() -> str:
    return os.pathsep.join(filter(None, (os.environ.get("PATH", ""), _BUNDLED)))


def find(name: str) -> str:
    """Return the path to a tool, honouring overrides such as CXY_CONAN."""
    requested = os.environ.get(_variable(name), name)
    path = shutil.which(requested, path=search_path())
    if path is None:
        raise CxyError(f"could not find {requested}")
    return path


def _variable(name: str) -> str:
    return f"CXY_{name.upper().replace('-', '_')}"


def command(name: str, pin: str | None) -> list[str]:
    """Return the command for a tool, at a pinned version if given.

    Pinned tools run through uv, which caches them.
    """
    if pin is None or _variable(name) in os.environ:
        return [find(name)]
    try:
        uv = find("uv")
    except CxyError:
        raise CxyError(f"{name} is pinned to {pin}, which needs uv to be installed") from None
    return [uv, "tool", "run", "--quiet", "--from", f"{_PACKAGES.get(name, name)}=={pin}", name]


def prepare(name: str, pins: Mapping[str, str]) -> list[str]:
    """Return the command for a formatter or linter after making sure that it runs.

    This also installs a pinned tool before many threads need it, and reports a broken tool
    once instead of for every file.
    """
    tool = command(name, pins.get(name))
    result = subprocess.run(
        [*tool, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    if result.returncode:
        raise CxyError(f"cannot run {name}: {result.stderr.strip()}")
    return tool


def nearest_config(path: Path, names: Sequence[str]) -> Path | None:
    """Return the configuration file that a tool will use for a path."""
    for directory in path.parents:
        for name in names:
            config = directory / name
            if not config.is_file():
                continue
            if name != "pyproject.toml" or b"[tool.ruff" in config.read_bytes():
                return config
    return None


def has_config(path: Path, names: Sequence[str]) -> bool:
    return nearest_config(path, names) is not None


def identity(name: str) -> list[str | int]:
    """Identify a tool's installed version without running it."""
    path = Path(find(name)).resolve()
    return [str(path), path.stat().st_mtime_ns]


def run(name: str, *arguments: str, cwd: Path, capture: bool = False) -> str:
    """Run a tool, returning its stdout if captured. Stderr is always shown."""
    command = [find(name), *arguments]
    sys.stdout.flush()
    process = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, "PATH": search_path()},
        stdout=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.returncode:
        raise CxyError(f"{name} failed with exit code {process.returncode}")
    return process.stdout or ""


def execute(program: Path, arguments: Sequence[str], cwd: Path | None = None) -> int:
    """Run a built program, by default in the caller's directory, and return its exit code."""
    code = subprocess.run([str(program), *arguments], cwd=cwd).returncode
    return 128 - code if code < 0 else code


def status(verb: str, message: str) -> None:
    print(f"{verb:>12} {message}", file=sys.stderr, flush=True)


def count(number: int, noun: str = "file") -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def relative(path: Path) -> str:
    """Return a path to show the user, relative to the current directory if possible."""
    try:
        return os.path.relpath(path)
    except ValueError:  # On Windows, it may be on another drive.
        return str(path)
