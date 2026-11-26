"""Drive Conan, CMake and Ninja, running each only when needed."""

import json
import os
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cxy.core import closure, cmake, conan, formatting, testing, tools
from cxy.core import lock as locks
from cxy.core import options as package_options
from cxy.core.conan import CMakeInfo
from cxy.core.errors import CxyError
from cxy.core.lock import Lock
from cxy.core.project import Project, Sources
from cxy.core.stamp import Stamp, digest, write_if_changed
from cxy.core.testing import TestProgram

NO_TESTS_RUN = 2
CACHE_MARKER = "CACHEDIR.TAG"
_CACHE_TEXT = "Signature: 8a477f597d28d172789f06886806bc55\n# Created by cxy.\n"


@dataclass(frozen=True)
class BuildOptions:
    release: bool = False
    profile: str = "default"
    jobs: int | None = None
    refresh: bool = False
    locked: bool = False
    verbose: bool = False

    @property
    def build_type(self) -> str:
        return "Release" if self.release else "Debug"


def build(project: Project, options: BuildOptions) -> None:
    _build(project, options, ["all"])


def run(project: Project, options: BuildOptions, arguments: Sequence[str]) -> int:
    programs = project.sources().programs
    if not programs:
        raise CxyError(f"nothing to run: no main.cpp in {project.manifest.layout.src}/")
    target = programs[0].component.target
    directory = _build(project, options, [target])
    return tools.execute(_program(directory, target), arguments)


def test(
    project: Project,
    options: BuildOptions,
    arguments: Sequence[str],
    names: Sequence[str] = (),
    isolated: bool = False,
    name_filter: str | None = None,
    ctest: bool = False,
) -> int:
    sources = project.sources()
    programs = testing.plan(project, sources)
    if not programs:
        raise CxyError("no tests found")
    setting = project.manifest.test_working_directory
    if setting is not None and not (project.root / setting).is_dir():
        raise CxyError(f"test.working-directory {setting} is not a directory")
    everything = programs
    programs = _select(project, programs, names)
    used = {program.framework.package for program in programs}
    if arguments and len(used) > 1 and not ctest:
        raise CxyError(
            f"these tests use {' and '.join(sorted(used))}, which take different arguments; "
            "use --filter, or --test to choose tests that use one of them"
        )
    needed = None
    if isolated:
        if ctest:
            raise CxyError("--isolated and --ctest cannot be combined")
        if not names:
            raise CxyError("--isolated needs --test to choose the tests to build")
        if not all(program.is_unit for program in programs):
            directory = project.manifest.layout.tests
            raise CxyError(
                f"--isolated needs a test that is its own program, and those in {directory}/ "
                "share one; set separate = true in the [test] section of cxy.toml, or move the "
                "test beside its source"
            )
        needed = {
            program.sources[0]: sorted(
                {file for s in program.sources for file in closure.needed(project, sources, s)}
            )
            for program in everything
            if program.is_unit
        }
    targets = [(p, p.isolated_target if isolated else p.target) for p in programs]
    directory = _build(project, options, [target for _, target in targets], needed)

    if ctest:
        chosen = [program.target for program in programs] if names else []
        return _run_ctest(directory, options, chosen, name_filter, arguments)

    # The same as under CTest, wherever cxy was called from.
    cwd = directory if setting is None else project.root / setting
    failed: list[tuple[str, int]] = []
    matched = False
    for program, target in targets:
        framework = program.framework
        executable = _program(directory, target)
        tools.status("Running", target)
        chosen = framework.filter_arguments(name_filter) if name_filter else []
        chosen += arguments
        code = tools.execute(executable, chosen, cwd)
        if framework.ran_nothing(executable, chosen, code, cwd):
            # A filter need not match something in every program.
            continue
        matched = True
        if code:
            failed.append((target, code))
    if not matched:
        tools.status("Failed", "no tests matched")
        return NO_TESTS_RUN
    if failed:
        tools.status("Failed", ", ".join(target for target, _ in failed))
    return failed[0][1] if failed else 0


def _run_ctest(
    directory: Path,
    options: BuildOptions,
    targets: Sequence[str],
    name_filter: str | None,
    arguments: Sequence[str],
) -> int:
    command = ["--no-tests=error", "--verbose" if options.verbose else "--output-on-failure"]
    if options.jobs is not None:
        command += ["--parallel", str(options.jobs)]
    if targets or name_filter:
        command += ["--tests-regex", ctest_pattern(targets, name_filter)]
    # Prefer the one that belongs to the CMake that configured the project.
    cmake_path = Path(tools.find("cmake"))
    program = cmake_path.with_name(f"ctest{cmake_path.suffix}")
    if not program.is_file():
        program = Path(tools.find("ctest"))
    tools.status("Running", "ctest")
    return tools.execute(program, [*command, *arguments], directory)


def ctest_pattern(targets: Sequence[str], name_filter: str | None) -> str:
    """Return a regular expression for tests, which are named "<target>:<name>"."""

    def escape(text: str) -> str:
        return re.sub(r"([.^$*+?()\[\]|\\])", r"\\\1", text)

    # Without a target, any: but only look for the text after the prefix.
    prefix = f"({'|'.join(map(escape, targets))})" if targets else "[^:]*"
    return f"^{prefix}:" + (f".*{escape(name_filter)}" if name_filter else "")


def _select(
    project: Project, programs: list[TestProgram], names: Sequence[str]
) -> list[TestProgram]:
    if not names:
        return programs
    selected: list[TestProgram] = []
    together = (project.manifest.layout.tests, f"{project.manifest.name}_tests")
    for name in names:
        path = Path(os.path.abspath(name))
        matches = [
            program
            for program in programs
            if name == program.target
            or (
                program.is_unit
                and (
                    name in closure.test_names(project, program.sources[0])
                    or path == project.root / program.sources[0]
                )
            )
            or (program.in_tests and name in together)
        ]
        if not matches:
            available = ", ".join(p.sources[0] if p.is_unit else p.target for p in programs)
            raise CxyError(f"no test called {name!r}; available: {available}")
        selected += [program for program in matches if program not in selected]
    return selected


def export(project: Project, options: BuildOptions, force: bool = False) -> None:
    """Write Conan and CMake files to the project root for use without cxy."""
    sources = project.sources()
    directory = _prepare(project, options, sources)
    graph = _install(project, options, directory)
    files = {
        project.root / "conanfile.py": conan.conanfile(project.manifest, standalone=True),
        project.root / "CMakeLists.txt": cmake.cmakelists(
            project.manifest, sources, graph, programs=testing.plan(project, sources)
        ),
    }
    if (lock := Lock.load(project.root / locks.FILENAME)) is not None:
        files[project.root / "conan.lock"] = lock.conan_text()
    record = project.build_dir / "exported.json"
    exported = _read_exported(record)
    conflicts = []
    for path, text in files.items():
        formatted = _format_export(project, path, text)
        files[path] = formatted
        if path.exists():
            current = path.read_text(encoding="utf-8", errors="replace")
            # Unformatted files are ours too, from before the formatter was available.
            ours = digest(path) == exported.get(path.name)
            if not ours and current not in (text, formatted):
                conflicts.append(path.name)
    if conflicts and not force:
        raise CxyError(
            f"{' and '.join(conflicts)} would be replaced, but cxy did not write what is there "
            "now; use --force to replace"
        )
    for path, text in files.items():
        if write_if_changed(path, text):
            tools.status("Wrote", path.name)
        else:
            tools.status("Unchanged", path.name)
    # Files that have not been edited since may be replaced without asking.
    write_if_changed(record, json.dumps({path.name: digest(path) for path in files}, indent=4))


def _read_exported(record: Path) -> dict[str, str]:
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def lock(
    project: Project,
    options: BuildOptions,
    *,
    check: bool = False,
    upgrade: bool = False,
    upgrade_packages: Sequence[str] = (),
) -> None:
    """Bring cxy.lock up to date without installing anything."""
    create_build_dir(project)
    ensure_profile(project, options)
    conanfile = _write_conanfile(project)
    directory = project.build_dir / options.build_type.lower()
    path = project.root / locks.FILENAME
    current = Lock.load(path)
    base = current
    update: list[str] = []
    if check:
        if upgrade or upgrade_packages:
            raise CxyError("--check cannot be combined with upgrading")
        _require_current(project, current)
    elif upgrade:
        base = None
        update = ["--update"]
    else:
        for name in upgrade_packages:
            if base is None:
                raise CxyError(f"there is no {locks.FILENAME} to upgrade")
            base = base.without_package(name)
            update.append(f"--update={name}")
    arguments, output = _lock_arguments(project, directory, base, locked=check)
    arguments += update
    # "lock create" accepts requirements that are not locked, whatever its arguments, so
    # checking relies on this alone.
    if check or _has_options(project):
        _inspect_graph(project, options, conanfile, arguments, locked=check)
    if not check:
        tools.run(
            "conan",
            *("lock", "create", str(conanfile)),
            *_conan_arguments(project, options),
            *arguments,
            cwd=project.root,
            capture=True,
        )
    if check:
        tools.status("Checked", f"{locks.FILENAME} is up to date")
    elif not _save_lock(project, current, output):
        tools.status("Unchanged", locks.FILENAME)


def _lock_arguments(
    project: Project, directory: Path, base: Lock | None, *, locked: bool
) -> tuple[list[str], Path]:
    """Return Conan's lockfile arguments, and where it will write the new lockfile."""
    output = directory / "lock" / "out.lock"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    arguments = [f"--lockfile-out={output}"]
    if base is None:
        return ["--lockfile=", *arguments], output
    source = directory / "lock" / "in.lock"
    arguments.append(f"--lockfile={source}")
    if not locked:
        arguments.append("--lockfile-partial")
        if not base.matches(project.manifest):
            # Drop what is no longer needed. Otherwise keep everything, since some of it may
            # be for other platforms.
            arguments.append("--lockfile-clean")
        base = base.without_overrides()
    write_if_changed(source, base.conan_text())
    return arguments, output


def _save_lock(project: Project, before: Lock | None, output: Path) -> bool:
    """Update cxy.lock from a lockfile written by Conan."""
    try:
        conan_lock = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CxyError(f"cannot read the lockfile that Conan wrote: {error}") from error
    after = Lock(locks.inputs(project.manifest), conan_lock)
    if not after.save(project.root / locks.FILENAME):
        return False
    tools.status("Locked", f"{tools.count(len(after.packages()), 'package')}")
    if before is not None:
        for change in locks.changes(before, after):
            tools.status("", change)
    return True


def _require_current(project: Project, lock: Lock | None) -> None:
    if lock is None:
        raise CxyError(f"there is no {locks.FILENAME}; run 'cxy lock'")
    if not lock.matches(project.manifest):
        raise CxyError(f"{locks.FILENAME} does not match cxy.toml; run 'cxy lock'")


def _run_locked(
    project: Project, locked: bool, command: Sequence[str], arguments: Sequence[str]
) -> str:
    try:
        return tools.run("conan", *command, *arguments, cwd=project.root, capture=True)
    except CxyError as error:
        if not locked:
            raise
        raise CxyError(
            f"{error}; if a requirement is not in {locks.FILENAME}, run 'cxy lock' with this "
            "platform and profile"
        ) from error


def _has_options(project: Project) -> bool:
    manifest = project.manifest
    return any(d.options for d in (*manifest.dependencies, *manifest.dev_dependencies))


def _inspect_graph(
    project: Project,
    options: BuildOptions,
    conanfile: Path,
    arguments: Sequence[str],
    *,
    locked: bool,
) -> None:
    """Resolve dependencies without installing them, and check the options given to them."""
    graph = _run_locked(
        project,
        locked,
        ["graph", "info", str(conanfile), *_conan_arguments(project, options)],
        # Nothing is built, but this avoids warnings about binaries that do not exist yet.
        [*arguments, "--build=missing", "--format=json"],
    )
    package_options.check(project.manifest, graph, project.root)


def _write_conanfile(project: Project) -> Path:
    path = project.build_dir / "conanfile.py"
    write_if_changed(path, conan.conanfile(project.manifest))
    return path


def _conan_arguments(project: Project, options: BuildOptions) -> list[str]:
    return [
        f"--profile:all={options.profile}",
        f"--settings:host=build_type={options.build_type}",
        f"--settings:host=compiler.cppstd={project.manifest.std}",
        "-vverbose" if options.verbose else "-vwarning",
    ]


def _format_export(project: Project, path: Path, text: str) -> str:
    draft = project.build_dir / "export" / path.name
    write_if_changed(draft, text)
    try:
        return formatting.format_text(text, draft, path, project.manifest.tools)
    except CxyError as error:
        tools.status("Note", f"{path.name} is not formatted: {error}")
        return text


def clean(project: Project) -> None:
    directory = project.build_dir
    if not directory.exists():
        return
    # layout.build is user input: never delete a directory that cxy did not create.
    if not (directory / CACHE_MARKER).is_file():
        raise CxyError(f"{directory} was not created by cxy, so it will not be deleted")
    shutil.rmtree(directory)
    tools.status("Removed", str(directory))


def _prepare(project: Project, options: BuildOptions, sources: Sources) -> Path:
    # Report project errors before running tools.
    cmake.cmakelists(project.manifest, sources, {}, programs=testing.plan(project, sources))
    create_build_dir(project)
    return project.build_dir / options.build_type.lower()


def _build(
    project: Project,
    options: BuildOptions,
    targets: Sequence[str],
    isolated: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    directory = configure(project, options, isolated)
    tools.status("Building", f"{project.manifest.name} ({options.build_type})")
    arguments = ["--build", str(directory), "--target", *targets]
    if options.jobs is not None:
        arguments += ["--parallel", str(options.jobs)]
    if options.verbose:
        arguments.append("--verbose")
    tools.run("cmake", *arguments, cwd=project.root)
    return directory


def configure(
    project: Project,
    options: BuildOptions,
    isolated: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    """Bring the CMake build directory up to date, and return it."""
    sources = project.sources()
    directory = _prepare(project, options, sources)
    graph = _install(project, options, directory)
    root = Path(os.path.relpath(project.root, project.build_dir)).as_posix()
    text = cmake.cmakelists(
        project.manifest,
        sources,
        graph,
        programs=testing.plan(project, sources),
        root=root,
        isolated=isolated,
    )
    _configure(project, options, directory, text)
    return directory


def create_build_dir(project: Project) -> None:
    directory = project.build_dir
    if directory.exists():
        return
    directory.mkdir(parents=True)
    (directory / CACHE_MARKER).write_text(_CACHE_TEXT, encoding="utf-8")


def _install(project: Project, options: BuildOptions, directory: Path) -> dict[str, CMakeInfo]:
    conanfile = _write_conanfile(project)
    lock_path = project.root / locks.FILENAME
    lock = Lock.load(lock_path)
    if options.locked:
        _require_current(project, lock)

    output = directory / "conan"
    graph_file = output / "graph.json"
    toolchain = output / "conan_toolchain.cmake"
    ensure_profile(project, options)

    def stamp() -> Stamp:
        return Stamp(
            directory / "conan.stamp",
            conan.conanfile(project.manifest),
            options.profile,
            _conan_config(options.profile),
            project.manifest.std,
            tools.identity("conan"),
            lock_path.read_text(encoding="utf-8") if lock_path.exists() else None,
        )

    if lock is not None and not options.refresh and stamp().is_current(graph_file, toolchain):
        return conan.read_graph(graph_file.read_text(encoding="utf-8"))

    stamp().clear()
    tools.status("Resolving", "dependencies")
    arguments, new_lock = _lock_arguments(project, directory, lock, locked=options.locked)
    if _has_options(project):
        # Before installing, which may spend a long time building the wrong thing.
        _inspect_graph(project, options, conanfile, arguments, locked=options.locked)
    stdout = _run_locked(
        project,
        options.locked,
        ["install", str(conanfile), *_conan_arguments(project, options)],
        [
            *arguments,
            f"--output-folder={output}",
            "--build=missing",
            "--format=json",
            "--conf:host=tools.cmake.cmaketoolchain:generator=Ninja",
            "--conf:host=tools.cmake.cmaketoolchain:user_presets=",
        ],
    )
    graph = conan.read_graph(stdout)
    write_if_changed(graph_file, stdout)
    if not options.locked:
        _save_lock(project, lock, new_lock)
    # Saving the lock changes the inputs.
    stamp().save()
    return graph


def _configure(project: Project, options: BuildOptions, directory: Path, text: str) -> None:
    generated = project.build_dir
    write_if_changed(generated / "CMakeLists.txt", text)

    toolchain = directory / "conan" / "conan_toolchain.cmake"
    # Dependency files can change while the toolchain stays the same, so the stamp covers
    # everything Conan generated.
    conan_files = sorted((directory / "conan").glob("*.cmake"))
    stamp = Stamp(
        directory / "cmake.stamp",
        text,
        [[p.name, p.read_text(encoding="utf-8")] for p in conan_files],
        tools.identity("cmake"),
        tools.identity("ninja"),
        [os.environ.get(name) for name in ("CC", "CXX", "CXXFLAGS", "LDFLAGS")],
    )
    if not options.refresh and stamp.is_current(directory / "build.ninja"):
        return
    stamp.clear()
    tools.status("Configuring", project.manifest.name)
    tools.run(
        "cmake",
        *(["--fresh"] if (directory / "CMakeCache.txt").exists() else []),
        "-S",
        str(generated),
        "-B",
        str(directory),
        "-G",
        "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={Path(tools.find('ninja')).as_posix()}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain.as_posix()}",
        f"-DCMAKE_BUILD_TYPE={options.build_type}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "--log-level=" + ("VERBOSE" if options.verbose else "WARNING"),
        cwd=project.root,
        # CMake reports results on stdout; only its warnings and errors matter here.
        capture=not options.verbose,
    )
    stamp.save()


def _conan_home() -> Path:
    return Path(os.environ.get("CONAN_HOME") or Path.home() / ".conan2")


def ensure_profile(project: Project, options: BuildOptions) -> None:
    if options.profile != "default" or os.environ.get("CONAN_DEFAULT_PROFILE"):
        return
    home = _conan_home()
    if not (home / "profiles" / "default").exists():
        tools.status("Detecting", "compiler for the default Conan profile")
        tools.run("conan", "profile", "detect", "-vwarning", cwd=project.root)


def _conan_config(profile: str) -> list[list[str]]:
    """Read the configuration that affects an install, including any included profiles."""
    home = _conan_home()
    paths = [home / "global.conf", home / "settings.yml", home / "remotes.json", Path(profile)]
    paths += sorted((home / "profiles").rglob("*"))
    return [
        [str(path), path.read_text(encoding="utf-8", errors="replace")]
        for path in paths
        if path.is_file()
    ]


def _program(directory: Path, name: str) -> Path:
    return directory / "bin" / (f"{name}.exe" if sys.platform == "win32" else name)
