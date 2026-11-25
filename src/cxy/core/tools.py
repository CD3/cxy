"""Find and run external tools."""

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from cxy.core.errors import CxyError

# Tools installed alongside cxy. Searched last, so the user's own tools win: switching
# between Conan versions would migrate the shared Conan cache back and forth.
_BUNDLED = str(Path(sys.executable).parent)


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

    Pinned tools run through uv, which caches them. They must be Python packages with the
    same name as the tool.
    """
    if pin is None or _variable(name) in os.environ:
        return [find(name)]
    try:
        uv = find("uv")
    except CxyError:
        raise CxyError(f"{name} is pinned to {pin}, which needs uv to be installed") from None
    return [uv, "tool", "run", "--quiet", "--from", f"{name}=={pin}", name]


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


def execute(program: Path, arguments: Sequence[str], *, quiet: bool = False) -> int:
    """Run a built program in the caller's directory and return its exit code."""
    output = subprocess.DEVNULL if quiet else None
    code = subprocess.run([str(program), *arguments], stdout=output, stderr=output).returncode
    return 128 - code if code < 0 else code


def status(verb: str, message: str) -> None:
    print(f"{verb:>12} {message}", file=sys.stderr, flush=True)
