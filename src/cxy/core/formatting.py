"""Format files with external tools.

Each tool produces formatted text and cxy does the rest, so that writing, checking and
showing differences behave alike for every tool.
"""

import difflib
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cxy.core import filetypes, tools
from cxy.core.errors import CxyError
from cxy.core.workspace import Workspace


@dataclass(frozen=True)
class Formatter:
    tool: str
    handles: Callable[[Path], bool]
    configs: tuple[str, ...]
    arguments: tuple[str, ...] = ()
    stdin: bool = False
    """Whether the tool reads text from stdin, rather than from the path it is given."""

    def format(self, command: Sequence[str], text: bytes, path: Path, name: Path) -> bytes:
        """Return formatted text. ``path`` holds the text; ``name`` is what it will be called."""
        target = name if self.stdin else path
        result = subprocess.run(
            [*command, *self.arguments, str(target), *(["-"] if self.stdin else [])],
            cwd=target.parent,
            # An empty input, rather than none, stops a tool from waiting on the terminal.
            input=text if self.stdin else b"",
            capture_output=True,
        )
        if result.returncode:
            detail = result.stderr.decode(errors="replace").strip()
            raise CxyError(detail or f"{self.tool} failed with exit code {result.returncode}")
        if not result.stdout.strip() and text.strip():
            # clang-format prints nothing for a file in .clang-format-ignore. Whatever the
            # reason, never replace a file's contents with nothing.
            return text
        return _keep_line_endings(text, result.stdout)


FORMATTERS = (
    Formatter(
        tool="clang-format",
        handles=filetypes.is_cpp,
        configs=(".clang-format", "_clang-format"),
    ),
    Formatter(
        tool="gersemi",
        handles=filetypes.is_cmake,
        configs=(".gersemirc",),
    ),
    Formatter(
        tool="ruff",
        handles=filetypes.is_conanfile,
        configs=("ruff.toml", ".ruff.toml", "pyproject.toml"),
        arguments=("format", "--force-exclude", "--stdin-filename"),
        stdin=True,
    ),
)


@dataclass(frozen=True)
class Result:
    path: Path
    before: bytes = b""
    after: bytes = b""
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.before != self.after


def fmt(
    workspace: Workspace, paths: Sequence[str] = (), *, check: bool = False, diff: bool = False
) -> int:
    """Format files, or with ``check`` or ``diff`` only report. Return an exit code."""
    files = workspace.files(paths)
    _reject_unsupported(paths)
    results: list[Result] = []
    failures = 0
    for formatter in FORMATTERS:
        chosen = [path for path in files if formatter.handles(path)]
        if not chosen:
            continue
        try:
            command = tools.prepare(formatter.tool, workspace.pins)
        except CxyError as error:
            tools.status("Failed", f"{error}; skipped {tools.count(len(chosen))}")
            failures += len(chosen)
            continue
        if not any(tools.has_config(path, formatter.configs) for path in chosen):
            tools.status(
                "Note", f"no {formatter.configs[0]} found; using {formatter.tool} defaults"
            )
        results += _format_all(formatter, command, chosen)

    changed = 0
    for result in sorted(results, key=lambda result: result.path):
        label = os.path.relpath(result.path)
        error = result.error
        if error is None and result.changed:
            if diff:
                _print_diff(label, result)
            elif check:
                tools.status("Unformatted", label)
            else:
                error = _write(result)
                if error is None:
                    tools.status("Formatted", label)
            changed += error is None
        if error is not None:
            tools.status("Failed", f"{label}: {error}")
            failures += 1

    unchanged = sum(not r.changed and r.error is None for r in results)
    report_only = check or diff
    verb = "would be formatted" if report_only else "formatted"
    summary = f"{tools.count(changed)} {verb}, {unchanged} unchanged"
    if failures:
        summary += f", {failures} failed"
    tools.status("Finished", summary)
    return 1 if failures or (report_only and changed) else 0


def format_text(text: str, path: Path, name: Path, pins: Mapping[str, str]) -> str:
    """Format generated text, which is stored in ``path`` and will be called ``name``."""
    for formatter in FORMATTERS:
        if formatter.handles(name):
            command = tools.prepare(formatter.tool, pins)
            return formatter.format(command, text.encode(), path, name).decode()
    return text


def _format_all(
    formatter: Formatter, command: Sequence[str], paths: Iterable[Path]
) -> list[Result]:
    def one(path: Path) -> Result:
        try:
            before = path.read_bytes()
            return Result(path, before, formatter.format(command, before, path, path))
        except (OSError, CxyError) as error:
            return Result(path, error=str(error))

    with ThreadPoolExecutor(os.cpu_count()) as pool:
        return list(pool.map(one, paths))


def _write(result: Result) -> str | None:
    try:
        if result.path.read_bytes() != result.before:
            return "changed while being formatted"
        result.path.write_bytes(result.after)
    except OSError as error:
        return str(error)
    return None


def _reject_unsupported(paths: Sequence[str]) -> None:
    for name in paths:
        path = Path(name)
        if path.is_file() and not any(f.handles(path) for f in FORMATTERS):
            raise CxyError(f"do not know how to format {name}")


def _keep_line_endings(before: bytes, after: bytes) -> bytes:
    """Undo a tool's conversion of CRLF, which gersemi does."""
    if b"\r\n" in before and before.count(b"\r\n") == before.count(b"\n"):
        return after.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return after


def _print_diff(label: str, result: Result) -> None:
    def lines(text: bytes) -> list[str]:
        return text.decode(errors="replace").splitlines(keepends=True)

    for line in difflib.unified_diff(
        lines(result.before), lines(result.after), f"a/{label}", f"b/{label}"
    ):
        print(line, end="" if line.endswith("\n") else "\n\\ No newline at end of file\n")
