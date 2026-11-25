"""Drive Conan, CMake and Ninja, running each only when needed."""

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cxy.core import closure, cmake, conan, formatting, tools
from cxy.core.cmake import TestProgram
from cxy.core.conan import CMakeInfo
from cxy.core.errors import CxyError
from cxy.core.project import Project, Sources
from cxy.core.stamp import Stamp, write_if_changed

NO_TESTS_RUN = 2  # Catch2's exit code, though some versions also use it for failures.
CACHE_MARKER = "CACHEDIR.TAG"
_CACHE_TEXT = "Signature: 8a477f597d28d172789f06886806bc55\n# Created by cxy.\n"


@dataclass(frozen=True)
class BuildOptions:
    release: bool = False
    profile: str = "default"
    jobs: int | None = None
    refresh: bool = False
    verbose: bool = False

    @property
    def build_type(self) -> str:
        return "Release" if self.release else "Debug"


def build(project: Project, options: BuildOptions) -> None:
    _build(project, options, ["all"])


def run(project: Project, options: BuildOptions, arguments: Sequence[str]) -> int:
    if project.sources().main is None:
        raise CxyError(f"nothing to run: no main.cpp in {project.manifest.layout.src}/")
    directory = _build(project, options, [project.manifest.name])
    return tools.execute(_program(directory, project.manifest.name), arguments)


def test(
    project: Project,
    options: BuildOptions,
    arguments: Sequence[str],
    names: Sequence[str] = (),
    isolated: bool = False,
) -> int:
    sources = project.sources()
    programs = cmake.test_programs(project.manifest, sources)
    if not programs:
        raise CxyError("no tests found")
    programs = _select(project, programs, names)
    needed = None
    if isolated:
        if not names:
            raise CxyError("--isolated needs --test to choose the tests to build")
        if not all(program.is_unit for program in programs):
            directory = project.manifest.layout.tests
            raise CxyError(f"--isolated only works for tests beside sources, not {directory}/")
        needed = {test: closure.needed(project, sources, test) for test in sources.unit_tests}
    targets = [p.isolated_target if isolated else p.target for p in programs]
    directory = _build(project, options, targets, needed)

    failed: list[tuple[str, int]] = []
    matched = False
    for target in targets:
        executable = _program(directory, target)
        tools.status("Running", target)
        code = tools.execute(executable, arguments)
        if code == NO_TESTS_RUN and _matches_nothing(executable, arguments):
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


def _select(
    project: Project, programs: list[TestProgram], names: Sequence[str]
) -> list[TestProgram]:
    if not names:
        return programs
    selected: list[TestProgram] = []
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
            or (not program.is_unit and name == project.manifest.layout.tests)
        ]
        if not matches:
            available = ", ".join(p.sources[0] if p.is_unit else p.target for p in programs)
            raise CxyError(f"no test called {name!r}; available: {available}")
        selected += [program for program in matches if program not in selected]
    return selected


def _matches_nothing(executable: Path, arguments: Sequence[str]) -> bool:
    """Tell "no tests ran" apart from a program that failed with the same exit code."""
    return tools.execute(executable, [*arguments, "--allow-running-no-tests"], quiet=True) == 0


def export(project: Project, options: BuildOptions, force: bool = False) -> None:
    """Write Conan and CMake files to the project root for use without cxy."""
    sources = project.sources()
    directory = _prepare(project, options, sources)
    graph = _install(project, options, directory)
    files = {
        project.root / "conanfile.py": conan.conanfile(project.manifest, standalone=True),
        project.root / "CMakeLists.txt": cmake.cmakelists(project.manifest, sources, graph),
    }
    conflicts = []
    for path, text in files.items():
        formatted = _format_export(project, path, text)
        files[path] = formatted
        if path.exists():
            current = path.read_text(encoding="utf-8", errors="replace")
            # Unformatted files are ours too, from before the formatter was available.
            if current not in (text, formatted):
                conflicts.append(path.name)
    if conflicts and not force:
        raise CxyError(
            f"{' and '.join(conflicts)} already exist with different content; "
            "use --force to replace"
        )
    for path, text in files.items():
        if write_if_changed(path, text):
            tools.status("Wrote", path.name)
        else:
            tools.status("Unchanged", path.name)


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
    cmake.cmakelists(project.manifest, sources, {})  # Report project errors before running tools.
    _create_build_dir(project)
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
    text = cmake.cmakelists(project.manifest, sources, graph, root=root, isolated=isolated)
    _configure(project, options, directory, text)
    return directory


def _create_build_dir(project: Project) -> None:
    directory = project.build_dir
    if directory.exists():
        return
    directory.mkdir(parents=True)
    (directory / CACHE_MARKER).write_text(_CACHE_TEXT, encoding="utf-8")


def _install(project: Project, options: BuildOptions, directory: Path) -> dict[str, CMakeInfo]:
    conanfile = project.build_dir / "conanfile.py"
    text = conan.conanfile(project.manifest)
    write_if_changed(conanfile, text)

    output = directory / "conan"
    graph_file = output / "graph.json"
    toolchain = output / "conan_toolchain.cmake"
    _ensure_profile(project, options)
    stamp = Stamp(
        directory / "conan.stamp",
        text,
        options.profile,
        _conan_config(options.profile),
        project.manifest.std,
        tools.identity("conan"),
    )
    if not options.refresh and stamp.is_current(graph_file, toolchain):
        return conan.read_graph(graph_file.read_text(encoding="utf-8"))

    stamp.clear()
    tools.status("Resolving", "dependencies")
    stdout = tools.run(
        "conan",
        "install",
        str(conanfile),
        f"--output-folder={output}",
        "--build=missing",
        "--format=json",
        f"--profile:all={options.profile}",
        f"--settings:host=build_type={options.build_type}",
        f"--settings:host=compiler.cppstd={project.manifest.std}",
        "--conf:host=tools.cmake.cmaketoolchain:generator=Ninja",
        "--conf:host=tools.cmake.cmaketoolchain:user_presets=",
        "-vverbose" if options.verbose else "-vwarning",
        cwd=project.root,
        capture=True,
    )
    graph = conan.read_graph(stdout)
    write_if_changed(graph_file, stdout)
    stamp.save()
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
        f"-DCMAKE_MAKE_PROGRAM={tools.find('ninja')}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
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


def _ensure_profile(project: Project, options: BuildOptions) -> None:
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
