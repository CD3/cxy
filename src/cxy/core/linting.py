"""Lint files with external tools and merge what they find."""

import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from cxy.core import filetypes, pipeline, tools
from cxy.core.errors import CxyError
from cxy.core.pipeline import BuildOptions
from cxy.core.workspace import Workspace

DATABASE = "compile_commands.json"

# The checks clang-tidy enabled by default until it stopped having any. They need no
# configuration because they report likely bugs rather than style.
_DEFAULT_TIDY_CHECKS = "-*,clang-diagnostic-*,clang-analyzer-*"

_CMAKE_LINT_CONFIGS = [
    f"{prefix}cmake-format{suffix}"
    for prefix in (".", "")
    for suffix in ("", ".yaml", ".json", ".py")
]
# Line length, whitespace, newlines and indentation, where cmake-lint's defaults contradict
# gersemi's. Formatting is the business of fmt.
_CMAKE_LAYOUT_CODES = ["C0301", "C0303", "C0304", "C0305", "C0307", "C0321", "C0327"]

_TIDY = re.compile(
    r"(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): "
    r"(?P<severity>warning|error): (?P<message>.*) \[(?P<check>[^\]]+)\]"
)
_CPPCHECK = re.compile(
    r"(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): "
    r"(?P<severity>\w+): (?P<message>.*) \[(?P<check>[^\]]+)\]"
)
_RUFF = re.compile(
    r"(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): (?P<check>[A-Z]+\d+|\S+:) (?P<message>.*)"
)
_CMAKE_LINT = re.compile(
    r"(?P<path>.+?):(?P<line>\d+)(?:,(?P<column>\d+))?: \[(?P<check>\w+)\] (?P<message>.*)"
)


@dataclass(frozen=True, order=True)
class Finding:
    path: Path
    line: int
    column: int
    check: str
    message: str
    severity: str = "warning"
    detail: str = field(default="", compare=False)

    def __str__(self) -> str:
        position = f"{self.line}:{self.column}" if self.column else str(self.line)
        text = f"{tools.relative(self.path)}:{position}: "
        text += f"{self.severity}: {self.message} [{self.check}]"
        return f"{text}\n{self.detail}" if self.detail else text


@dataclass
class Report:
    findings: set[Finding] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Context:
    workspace: Workspace
    paths: tuple[str, ...]
    """What the user asked for, or empty for everything."""

    files: frozenset[Path]
    build_dir: Path | None
    options: BuildOptions
    fix: bool

    @property
    def jobs(self) -> int:
        return self.options.jobs or os.cpu_count() or 1

    def choose(self, handles: Callable[[Path], bool]) -> list[Path]:
        return sorted(path for path in self.files if handles(path))

    def keep(self, findings: Iterable[Finding]) -> set[Finding]:
        return {finding for finding in findings if finding.path in self.files}


def lint(
    workspace: Workspace,
    paths: Sequence[str] = (),
    *,
    fix: bool = False,
    deep: bool = False,
    build_dir: str | None = None,
    options: BuildOptions | None = None,
) -> int:
    """Report problems, fixing what can be fixed if asked. Return an exit code."""
    files = frozenset(path.resolve() for path in workspace.files(paths))
    known = (filetypes.is_cpp, filetypes.is_cmake, filetypes.is_conanfile)
    for name in paths:
        if Path(name).is_file() and not any(handles(Path(name)) for handles in known):
            raise CxyError(f"do not know how to lint {name}")
    options = options or BuildOptions()
    directory = Path(os.path.abspath(build_dir)) if build_dir else None
    context = Context(workspace, tuple(paths), files, directory, options, fix)

    report = Report()
    for linter in (_lint_cmake, _lint_conan, _lint_cpp):
        try:
            linter(context, report)
        except CxyError as error:
            report.failures.append(str(error))
    if deep:
        try:
            _lint_cpp_deep(context, report)
        except CxyError as error:
            report.failures.append(str(error))

    for finding in sorted(report.findings):
        print(finding)
    for failure in dict.fromkeys(report.failures):
        tools.status("Failed", failure)
    summary = tools.count(len(report.findings), "problem")
    if report.failures:
        summary += ", but not everything could be checked"
    tools.status("Finished", summary)
    return 1 if report.findings or report.failures else 0


def _lint_cmake(context: Context, report: Report) -> None:
    chosen = context.choose(filetypes.is_cmake)
    if not chosen:
        return
    try:
        command = tools.prepare("cmake-lint", context.workspace.pins)
    except CxyError as error:
        if "cmake-lint" in context.workspace.pins:
            raise
        # It is optional because it is no longer maintained.
        tools.status("Note", f"skipped {tools.count(len(chosen), 'CMake file')}: {error}")
        return

    def one(path: Path) -> None:
        # Run beside the file, where cmake-lint looks for its configuration.
        options = []
        if not tools.has_config(path, _CMAKE_LINT_CONFIGS):
            options = ["--disabled-codes", *_CMAKE_LAYOUT_CODES, "--"]
        result = _run([*command, *options, str(path)], path.parent)
        _collect("cmake-lint", result, _CMAKE_LINT, path.parent, path, context, report)

    _each(context, one, chosen)


def _lint_conan(context: Context, report: Report) -> None:
    chosen = context.choose(filetypes.is_conanfile)
    if not chosen:
        return
    command = tools.prepare("ruff", context.workspace.pins)
    root = context.workspace.root
    result = _run(
        [
            *command,
            "check",
            "--output-format=concise",
            "--force-exclude",
            *(["--fix"] if context.fix else []),
            *map(str, chosen),
        ],
        root,
    )
    _collect("ruff", result, _RUFF, root, root, context, report)


def _lint_cpp(context: Context, report: Report) -> None:
    chosen = context.choose(filetypes.is_cpp)
    if not chosen:
        return
    command = _prepare_cpp_tool("clang-tidy", context)
    database = _database(context)
    targets = _translation_units(context, database, report)
    if not targets:
        return
    if not any(tools.has_config(path, [".clang-tidy"]) for path in chosen):
        tools.status("Note", "no .clang-tidy found; using compiler warnings and static analysis")
    command += [f"-p={database.parent}", "--quiet"]
    root = context.workspace.root

    def tidy(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        options = list(extra)
        config = tools.nearest_config(path, [".clang-tidy"])
        if config is None:
            options.append(f"--checks={_DEFAULT_TIDY_CHECKS}")
        if config is None or "HeaderFilterRegex" not in config.read_text(errors="replace"):
            # Findings are filtered later, by real path. Fixes are applied by clang-tidy, so
            # they need a strict filter, which may miss headers included by a relative path.
            pattern = _header_filter(context) if "--fix" in extra else ".*"
            options.append(f"--header-filter={pattern}")
        return _run([*command, *options, str(path)], root)

    tools.status("Linting", tools.count(len(targets), "C++ file"))
    if context.fix:
        # One at a time, since several files may include, and fix, the same header.
        for path in targets:
            tidy(path, "--fix")

    def one(path: Path) -> None:
        _collect("clang-tidy", tidy(path), _TIDY, root, path, context, report)

    _each(context, one, targets)


def _lint_cpp_deep(context: Context, report: Report) -> None:
    if not context.choose(filetypes.is_cpp):
        return
    command = _prepare_cpp_tool("cppcheck", context)
    database = _database(context)
    root = context.workspace.root
    tools.status("Analyzing", "C++ files with cppcheck")
    result = _run(
        [
            *command,
            f"--project={database}",
            "--enable=warning,performance,portability",
            "--inline-suppr",
            "--quiet",
            f"-j{context.jobs}",
            "--template={file}:{line}:{column}: {severity}: {message} [{id}]",
            "--template-location=",
        ],
        root,
    )
    # cppcheck reports on stderr.
    merged = subprocess.CompletedProcess(result.args, result.returncode, result.stderr, "")
    _collect("cppcheck", merged, _CPPCHECK, root, root, context, report)


def _prepare_cpp_tool(name: str, context: Context) -> list[str]:
    try:
        return tools.prepare(name, context.workspace.pins)
    except CxyError as error:
        if name in context.workspace.pins:
            raise
        advice = f"install it, for example with 'uv tool install {name}'"
        raise CxyError(f"C++ files were not checked with {name}: {error}; {advice}") from error


def _database(context: Context) -> Path:
    """Find the compile database, configuring the project only if cxy manages it."""
    workspace = context.workspace
    if context.build_dir is not None:
        database = context.build_dir / DATABASE
        if not database.is_file():
            raise CxyError(f"C++ files were not checked: no {DATABASE} in {context.build_dir}")
        return database
    if workspace.project is not None:
        return pipeline.configure(workspace.project, context.options) / DATABASE
    database = find_database(workspace.root)
    if database is None:
        raise CxyError(
            f"C++ files were not checked: no {DATABASE} found; configure the project with "
            "CMAKE_EXPORT_COMPILE_COMMANDS=ON, or use --build-dir"
        )
    tools.status("Using", tools.relative(database))
    return database


def find_database(root: Path) -> Path | None:
    """Return the most recently written compile database in the usual places."""
    patterns = [DATABASE]
    for directory in ("build*", "cmake-build-*", "out", ".build"):
        patterns += [f"{directory}/{depth}{DATABASE}" for depth in ("", "*/", "*/*/")]
    found = {path for pattern in patterns for path in root.glob(pattern) if path.is_file()}
    return max(found, key=lambda path: (path.stat().st_mtime_ns, str(path)), default=None)


def read_database(database: Path) -> set[Path]:
    """Return the files in a compile database."""
    try:
        entries = json.loads(database.read_text(encoding="utf-8"))
        return {Path(entry["directory"], entry["file"]).resolve() for entry in entries}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CxyError(f"cannot read {database}: {error}") from error


def _translation_units(context: Context, database: Path, report: Report) -> list[Path]:
    compiled = read_database(database)
    sources = context.choose(filetypes.is_cpp_source)
    targets = [path for path in sources if path in compiled]
    missing = [path for path in sources if path not in compiled]
    named = {Path(os.path.abspath(name)).resolve() for name in context.paths}
    for path in missing:
        if path in named:
            report.failures.append(f"{tools.relative(path)} is not in {DATABASE}")
    if missing and not targets:
        report.failures.append(f"none of the C++ sources are in {database}")
    elif missing:
        example = tools.relative(missing[0])
        tools.status(
            "Note",
            f"skipped {tools.count(len(missing))} not in {DATABASE}, such as {example}",
        )
    # Headers are normally checked through the sources that include them. One named on its
    # own is checked directly, and clang-tidy infers how to compile it.
    headers = [path for path in context.choose(filetypes.is_cpp_header) if path in named]
    return [*targets, *headers]


def _header_filter(context: Context) -> str:
    """Return a regular expression for the headers to fix."""
    root = context.workspace.root
    if context.paths:
        roots = {Path(os.path.abspath(name)).resolve() for name in context.paths}
    else:
        # The top-level directories holding source files, which leaves out build directories.
        roots = {
            root / path.relative_to(root).parts[0]
            for path in context.choose(filetypes.is_cpp)
            if path.is_relative_to(root)
        }
    return f"^({'|'.join(_escape(str(path)) for path in sorted(roots))})(/|$)"


def _escape(text: str) -> str:
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", text)


def _run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _each(context: Context, function: Callable[[Path], None], paths: Iterable[Path]) -> None:
    with ThreadPoolExecutor(context.jobs) as pool:
        # Consume the results so that exceptions are raised.
        list(pool.map(function, paths))


def _collect(
    tool: str,
    result: subprocess.CompletedProcess[str],
    pattern: re.Pattern[str],
    cwd: Path,
    subject: Path,
    context: Context,
    report: Report,
) -> None:
    """Add the results of checking a file or directory to the report."""
    found = parse(pattern, result.stdout, cwd)
    kept = context.keep(found)
    report.findings |= kept
    if result.returncode == 0 or kept:
        return
    # A tool exits with an error for its findings, which is fine even if they are in files
    # that were not asked for. Anything else means that it did not check everything.
    broken = [finding for finding in found if finding.check.startswith("clang-diagnostic-error")]
    if found and not broken:
        return
    if broken:
        detail = str(broken[0])
    else:
        # The last line says what went wrong, after any traceback.
        output = result.stderr.strip() or result.stdout.strip()
        detail = output.splitlines()[-1] if output else f"exit code {result.returncode}"
    report.failures.append(f"{tool} could not check {tools.relative(subject)}: {detail}")


def parse(pattern: re.Pattern[str], output: str, cwd: Path) -> list[Finding]:
    """Parse a tool's output. Lines after a finding that do not start another are its detail."""
    matches: list[tuple[Mapping[str, str | None], list[str]]] = []
    for line in output.splitlines():
        if match := pattern.fullmatch(line):
            matches.append((match.groupdict(), []))
        elif matches and pattern is _TIDY:
            matches[-1][1].append(line)
    return [
        Finding(
            path=Path(os.path.normpath(cwd / (values["path"] or ""))).resolve(),
            line=int(values["line"] or 0),
            column=int(values.get("column") or 0),
            check=(values["check"] or "").rstrip(":"),
            message=values["message"] or "",
            severity=values.get("severity") or "warning",
            detail="\n".join(detail),
        )
        for values, detail in matches
    ]
