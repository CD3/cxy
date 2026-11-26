"""Write a cxy.toml for an existing CMake and Conan project, without changing the project.

The project's own tools say what it builds: Conan resolves its recipe, and CMake configures
it with a File API query, in cxy's build directory. What cxy cannot build the same way is
reported, never silently dropped.
"""

import ast
import json
import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

import tomlkit

from cxy.core import closure, pipeline, testing, tools
from cxy.core import manifest as manifests
from cxy.core.errors import CxyError
from cxy.core.manifest import STANDARDS, Layout, Manifest, OptionValue
from cxy.core.pipeline import BuildOptions
from cxy.core.project import Project, Sources

_CLIENT = "client-cxy"
_LIBRARIES = {"STATIC_LIBRARY", "SHARED_LIBRARY", "OBJECT_LIBRARY", "MODULE_LIBRARY"}
# Commands that give a target its own settings, rather than passing on a dependency's.
_DEFINITION_COMMANDS = {
    "add_compile_definitions",
    "add_definitions",
    "set_directory_properties",
    "set_property",
    "set_target_properties",
    "target_compile_definitions",
}
_OPTION_COMMANDS = {"add_compile_options", "set_property", "target_compile_options"}
_LINK_OPTION_COMMANDS = {"add_link_options", "set_property", "target_link_options"}
_STANDARD_COMMANDS = {"set_property", "set_target_properties", "target_compile_features"}
_CMAKE_OPTIONS_IGNORED = {"BUILD_TESTING"}


@dataclass
class Findings:
    problems: list[str] = field(default_factory=list)
    """What cxy would build differently."""

    warnings: list[str] = field(default_factory=list)
    """What to check."""


@dataclass(frozen=True)
class Requirement:
    name: str
    version: str
    test: bool = False
    force: bool = False
    options: Mapping[str, OptionValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Recipe:
    name: str | None = None
    version: str | None = None
    requirements: tuple[Requirement, ...] = ()
    overrides: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    file_name: str | None = None
    sources: tuple[str, ...] = ()
    """Compiled project files, relative to the project root."""

    foreign: tuple[str, ...] = ()
    """Compiled files from outside the project, or generated while building."""

    languages: frozenset[str] = frozenset({"CXX"})
    includes: tuple[str, ...] = ()
    """Project directories searched for headers, relative to the project root."""

    definitions: tuple[str, ...] = ()
    """Preprocessor definitions set by the project, not by its dependencies."""

    flags: tuple[str, ...] = ()
    """Compiler options set by the project."""

    link_flags: tuple[str, ...] = ()
    """Linker options set by the project."""

    inherited: tuple[str, ...] = ()
    """Definitions, options and libraries from the targets it links, such as packages."""

    standard: int | None = None
    standard_is_explicit: bool = False
    """Whether the project chose the standard for this target, rather than a variable."""


@dataclass(frozen=True)
class Provided:
    """What Conan's packages give the targets that use them."""

    folders: tuple[Path, ...] = ()
    system_libs: frozenset[str] = frozenset()
    flags: frozenset[str] = frozenset()

    def provides(self, item: str) -> bool:
        text = item.strip().strip('"')
        if text in self.flags or text.removeprefix("-D") in self.flags:
            return True
        if text.startswith("-framework"):
            return text.split()[-1] in self.system_libs
        if text.startswith("-l"):
            return text[2:] in self.system_libs
        if text.lower().endswith(".lib") and not Path(text).is_absolute():
            return text[:-4] in self.system_libs
        for prefix in ("/LIBPATH:", "-LIBPATH:", "-Wl,-rpath,", "-Wl,-rpath-link,", "-L"):
            if text.upper().startswith(prefix.upper()):
                return all(self.provides(part) for part in text[len(prefix) :].split(os.pathsep))
        path = Path(text)
        return path.is_absolute() and any(path.resolve().is_relative_to(f) for f in self.folders)


def conan_provides(install_output: str) -> Provided:
    """Read what the packages in ``conan install --format=json`` output provide."""
    nodes = json.loads(install_output)["graph"]["nodes"].values()
    folders, libs, flags = [], set(), set()
    for node in nodes:
        if node.get("package_folder"):
            folders.append(Path(node["package_folder"]).resolve())
        for component in (node.get("cpp_info") or {}).values():
            component = component or {}
            libs.update(component.get("system_libs") or [])
            libs.update(component.get("frameworks") or [])
            for key in ("cxxflags", "cflags", "defines", "sharedlinkflags", "exelinkflags"):
                flags.update(component.get(key) or [])
    return Provided(tuple(folders), frozenset(libs), frozenset(flags))


@dataclass(frozen=True)
class CMakeProject:
    name: str
    version: str | None = None
    targets: tuple[Target, ...] = ()
    mentions_standard: bool = False
    """Whether the project's CMake files set CMAKE_CXX_STANDARD."""

    toolchain_standard: int | None = None
    """The standard that the Conan toolchain set."""

    options: Mapping[str, str] = field(default_factory=dict)


def port(root: Path, options: BuildOptions, *, dry_run: bool = False, force: bool = False) -> None:
    target = root / manifests.FILENAME
    if target.exists() and not dry_run:
        raise CxyError(f"{target} already exists")
    if not (root / "CMakeLists.txt").is_file():
        raise CxyError(f"no CMakeLists.txt in {root}: only CMake projects can be ported for now")
    recipe_path = next(
        (root / name for name in ("conanfile.py", "conanfile.txt") if (root / name).is_file()),
        None,
    )
    if recipe_path is None:
        raise CxyError(f"no conanfile.py or conanfile.txt in {root}")

    placeholder = Project(root, Manifest(name="port", version="0"))
    pipeline.create_build_dir(placeholder)
    directory = placeholder.build_dir / "port"
    findings = Findings()
    graph = _install(root, recipe_path, directory / "conan", options)
    recipe = read_recipe(recipe_path, graph, findings)
    toolchain = next((directory / "conan").rglob("conan_toolchain.cmake"), None)
    if toolchain is None:
        raise CxyError("Conan did not write conan_toolchain.cmake")
    _configure(root, directory / "cmake", toolchain, options)
    cmake = read_cmake(
        directory / "cmake", root, toolchain.read_text(encoding="utf-8"), [directory]
    )
    text = propose(root, recipe, cmake, findings, conan_provides(graph))

    for problem in findings.problems:
        tools.status("Problem", problem)
    for warning in findings.warnings:
        tools.status("Warning", warning)
    if dry_run or (findings.problems and not force):
        print(text, end="", flush=True)
    if dry_run:
        return
    if findings.problems and not force:
        problems = tools.count(len(findings.problems), "problem")
        raise CxyError(
            f"cxy would build this project differently ({problems}), so "
            f"{manifests.FILENAME} was not written; use --force to write it anyway"
        )
    target.write_text(text, encoding="utf-8", newline="")
    tools.status("Created", f"{manifests.FILENAME}; check the warnings above, if any")


def _install(root: Path, recipe: Path, output: Path, options: BuildOptions) -> str:
    placeholder = Project(root, Manifest(name="port", version="0"))
    pipeline.ensure_profile(placeholder, options)
    tools.status("Resolving", f"the dependencies in {recipe.name}")
    # Files from an earlier install with other settings would also be found by CMake.
    shutil.rmtree(output, ignore_errors=True)
    return tools.run(
        "conan",
        "install",
        str(recipe),
        f"--profile:all={options.profile}",
        "--settings:host=build_type=Debug",
        f"--output-folder={output}",
        "--build=missing",
        "--format=json",
        "--generator=CMakeDeps",
        "--generator=CMakeToolchain",
        "--conf:host=tools.cmake.cmaketoolchain:generator=Ninja",
        # Leave the project's own files alone.
        "--conf:host=tools.cmake.cmaketoolchain:user_presets=",
        "-vverbose" if options.verbose else "-vwarning",
        cwd=root,
        capture=True,
    )


def _configure(root: Path, build: Path, toolchain: Path, options: BuildOptions) -> None:
    query = build / ".cmake" / "api" / "v1" / "query" / _CLIENT / "query.json"
    query.parent.mkdir(parents=True, exist_ok=True)
    requests = [
        {"kind": "codemodel", "version": 2},
        {"kind": "cache", "version": 2},
        {"kind": "cmakeFiles", "version": 1},
    ]
    query.write_text(json.dumps({"requests": requests}), encoding="utf-8")
    tools.status("Configuring", "the CMake project")
    tools.run(
        "cmake",
        *(["--fresh"] if (build / "CMakeCache.txt").exists() else []),
        "-S",
        str(root),
        "-B",
        str(build),
        "-G",
        "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={Path(tools.find('ninja')).as_posix()}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain.as_posix()}",
        "-DCMAKE_BUILD_TYPE=Debug",
        "--log-level=" + ("VERBOSE" if options.verbose else "WARNING"),
        cwd=root,
        capture=not options.verbose,
    )


# Conan


def read_recipe(path: Path, install_output: str, findings: Findings) -> Recipe:
    """Read a recipe's requirements from ``conan install --format=json`` and its text."""
    try:
        root = json.loads(install_output)["graph"]["nodes"]["0"]
        dependencies = list(root["dependencies"].values())
    except (ValueError, KeyError, TypeError) as error:
        raise CxyError("could not understand Conan's JSON output") from error
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        overrides, conditional = scan_recipe(text)
        declared = _option_list(root.get("default_options"))
        if conditional:
            findings.warnings.append(
                f"{path.name} chooses some requirements with conditions; these are the ones "
                "for this machine and profile"
            )
    else:
        overrides, declared = {}, _txt_options(text)

    found: list[tuple[str, str, bool, bool]] = []
    for dependency in dependencies:
        if not dependency.get("direct"):
            continue
        require = dependency.get("require") or dependency["ref"]
        if dependency.get("build") and require.partition("/")[0] in {"cmake", "ninja"}:
            findings.warnings.append(f"cxy uses its own CMake and Ninja, not {require}")
            continue
        if dependency.get("build"):
            findings.problems.append(
                f"{require} is a tool requirement, which cxy cannot use yet; the build may "
                "still find the tool on PATH"
            )
            continue
        reference = _reference(require, findings)
        if reference is None:
            continue
        name, version = reference
        if name in overrides:
            continue
        found.append((name, version, bool(dependency.get("test")), bool(dependency.get("force"))))

    options = _assign_options(declared, {name for name, *_ in found}, findings)
    requirements = tuple(
        Requirement(name, version, test=test, force=forced, options=options.get(name, {}))
        for name, version, test, forced in found
    )
    return Recipe(
        name=root.get("name"),
        version=root.get("version"),
        requirements=requirements,
        overrides=overrides,
    )


def scan_recipe(text: str) -> tuple[dict[str, str], bool]:
    """Return the overrides in a conanfile.py, and whether any requirement is conditional."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, False
    overrides: dict[str, str] = {}
    conditional = False

    def visit(node: ast.AST, guarded: bool) -> None:
        nonlocal conditional
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"requires", "test_requires", "tool_requires", "build_requires"}
        ):
            conditional = conditional or guarded
            override = any(
                k.arg == "override" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords
            )
            first = node.args[0] if node.args else None
            if override and isinstance(first, ast.Constant) and isinstance(first.value, str):
                name, _, version = first.value.partition("/")
                overrides[name] = version
        guarded_here = guarded or isinstance(
            node, (ast.If, ast.IfExp, ast.For, ast.While, ast.Try, ast.Match)
        )
        for child in ast.iter_child_nodes(node):
            visit(child, guarded_here)

    visit(tree, False)
    return overrides, conditional


def _reference(require: str, findings: Findings) -> tuple[str, str] | None:
    reference = require.partition("#")[0]
    if "@" in reference:
        findings.problems.append(
            f"{require} has a user or channel, which cxy cannot express; cxy.toml uses the "
            "plain name and version"
        )
        reference = reference.partition("@")[0]
    name, _, version = reference.partition("/")
    if not name or not version:
        findings.problems.append(f"cannot read the requirement {require!r}")
        return None
    return name, version


def _option_list(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        return [(str(k), str(v)) for k, v in value.items()]
    if isinstance(value, str):
        value = [value]
    if isinstance(value, (list, tuple)):
        return [
            (key.strip(), option.strip())
            for item in value
            for key, _, option in [str(item).partition("=")]
        ]
    return []


def _txt_options(text: str) -> list[tuple[str, str]]:
    section = None
    found = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
        elif section == "options" and "=" in line:
            key, _, value = line.partition("=")
            found.append((key.strip(), value.strip()))
    return found


def _assign_options(
    declared: Iterable[tuple[str, str]], direct: set[str], findings: Findings
) -> dict[str, dict[str, OptionValue]]:
    options: dict[str, dict[str, OptionValue]] = {}
    for key, value in declared:
        match = re.fullmatch(r"([^/*:&]+)(?:/\*)?:(.+)", key)
        if match is None or match[1] not in direct:
            findings.problems.append(
                f"the option {key}={value} is not for a direct dependency, and cxy can only set "
                "options of direct dependencies"
            )
            continue
        converted: OptionValue = {"True": True, "False": False}.get(value, value)
        options.setdefault(match[1], {})[match[2]] = converted
    return options


# CMake


def read_cmake(
    build: Path, root: Path, toolchain: str, ignored: Sequence[Path] = ()
) -> CMakeProject:
    """Read a configured build's File API replies.

    Files in ``ignored`` directories, such as cxy's own, do not count as the project's.
    """
    reply = build / ".cmake" / "api" / "v1" / "reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise CxyError(f"CMake wrote no File API reply in {reply}")
    try:
        index = _json(indexes[-1])
        responses = index["reply"][_CLIENT]["query.json"]["responses"]
        files = {response["kind"]: reply / response["jsonFile"] for response in responses}
        codemodel = _json(files["codemodel"])
        cache = _json(files["cache"])
        inputs = _json(files["cmakeFiles"])["inputs"]
    except (KeyError, TypeError) as error:
        raise CxyError(f"could not understand CMake's File API reply in {reply}") from error

    root = root.resolve()
    ignored = [path.resolve() for path in ignored]
    source = Path(codemodel["paths"]["source"])

    def project_path(text: str, base: Path = source) -> str | None:
        path = Path(os.path.normpath(base / text)).resolve()
        if not path.is_relative_to(root) or any(path.is_relative_to(d) for d in ignored):
            return None
        return path.relative_to(root).as_posix()

    configuration = codemodel["configurations"][0]
    project = next(p for p in configuration["projects"] if "parentIndex" not in p)
    entries = {entry["name"]: entry for entry in cache["entries"]}
    targets = []
    for summary in configuration["targets"]:
        data = _json(reply / summary["jsonFile"])
        if data["type"] == "UTILITY" or data.get("isGeneratorProvided"):
            continue
        targets.append(_target(data, project_path, lambda text: _built(text, build)))

    mentions = False
    for item in inputs:
        if item.get("isExternal") or item.get("isGenerated") or item.get("isCMake"):
            continue
        relative = project_path(item["path"])
        if relative is None:
            continue
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
        mentions = mentions or re.search(r"\bCMAKE_CXX_STANDARD\b", text) is not None

    toolchain_standard = re.search(r"set\(\s*CMAKE_CXX_STANDARD\s+(\d+)\s*\)", toolchain)
    version = entries.get("CMAKE_PROJECT_VERSION", {}).get("value") or None
    return CMakeProject(
        name=project["name"],
        version=version,
        targets=tuple(targets),
        mentions_standard=mentions,
        toolchain_standard=int(toolchain_standard[1]) if toolchain_standard else None,
        options=_cmake_options(cache["entries"]),
    )


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CxyError(f"cannot read {path}: {error}") from error


def _built(text: str, build: Path) -> bool:
    """Whether a library on a link line was built by this build, rather than found."""
    path = Path(text)
    return not path.is_absolute() or path.resolve().is_relative_to(build.resolve())


def _target(data: Mapping[str, Any], project_path: Any, built: Any) -> Target:
    graph = data.get("backtraceGraph") or {"nodes": [], "commands": [], "files": []}

    def origin(index: int | None) -> tuple[str | None, str | None]:
        """Return the command and file that set something."""
        if index is None:
            return None, None
        node = graph["nodes"][index]
        command = graph["commands"][node["command"]] if "command" in node else None
        return command, graph["files"][node["file"]]

    def own(index: int | None, commands: set[str]) -> bool:
        command, file = origin(index)
        return command in commands and file is not None and project_path(file) is not None

    sources, foreign = [], []
    for item in data.get("sources", []):
        if "compileGroupIndex" not in item:
            continue
        relative = None if item.get("isGenerated") else project_path(item["path"])
        if relative is None:
            foreign.append(item["path"])
        else:
            sources.append(relative)

    groups = data.get("compileGroups", [])
    includes: dict[str, None] = {}
    definitions: dict[str, None] = {}
    flags: dict[str, None] = {}
    link_flags: dict[str, None] = {}
    inherited: dict[str, None] = {}
    standard = None
    explicit = False
    for group in groups:
        for include in group.get("includes", []):
            relative = None if include.get("isSystem") else project_path(include["path"])
            if relative is not None:
                includes[relative] = None
        for definition in group.get("defines", []):
            if own(definition.get("backtrace"), _DEFINITION_COMMANDS):
                definitions[definition["define"]] = None
            elif definition.get("backtrace") is not None:
                inherited["-D" + definition["define"]] = None
        for fragment in group.get("compileCommandFragments", []):
            # Those without a backtrace come from variables such as CMAKE_CXX_FLAGS.
            if own(fragment.get("backtrace"), _OPTION_COMMANDS):
                flags[fragment["fragment"]] = None
            elif fragment.get("backtrace") is not None:
                inherited[fragment["fragment"]] = None
        if group.get("language") == "CXX" and "languageStandard" in group:
            text = group["languageStandard"].get("standard", "")
            if text.isdigit():
                standard = max(standard or 0, int(text))
            explicit = explicit or any(
                own(index, _STANDARD_COMMANDS)
                for index in group["languageStandard"].get("backtraces", [])
            )
    for fragment in (data.get("link") or {}).get("commandFragments", []):
        text = fragment["fragment"].strip()
        if fragment.get("backtrace") is None or not text:
            continue
        if own(fragment["backtrace"], _LINK_OPTION_COMMANDS):
            link_flags[text] = None
        elif fragment.get("role") != "libraries" or not built(text.strip('"')):
            inherited[text] = None
    return Target(
        name=data["name"],
        kind=data["type"],
        file_name=data.get("nameOnDisk"),
        sources=tuple(sources),
        foreign=tuple(foreign),
        languages=frozenset(group.get("language", "") for group in groups),
        includes=tuple(includes),
        definitions=tuple(definitions),
        flags=tuple(flags),
        link_flags=tuple(link_flags),
        inherited=tuple(inherited),
        standard=standard,
        standard_is_explicit=explicit,
    )


def _cmake_options(entries: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Return the project's boolean cache options, which option() creates."""
    found = {}
    for entry in entries:
        name = entry["name"]
        properties = {p["name"] for p in entry.get("properties", [])}
        if (
            entry.get("type") == "BOOL"
            and "ADVANCED" not in properties
            and name not in _CMAKE_OPTIONS_IGNORED
            and not name.startswith(("CMAKE_", "FETCHCONTENT_"))
        ):
            found[name] = entry.get("value", "")
    return found


# The manifest


def propose(
    root: Path,
    recipe: Recipe,
    cmake: CMakeProject,
    findings: Findings,
    provided: Provided | None = None,
) -> str:
    """Return the text of a cxy.toml that builds what the CMake project builds."""
    name = _package_name(recipe.name or cmake.name)
    version = recipe.version or cmake.version
    if version is None:
        findings.warnings.append("found no version, so package.version is 0.1.0")
        version = "0.1.0"

    targets = []
    for target in cmake.targets:
        if target.foreign:
            what = "is built from" if not target.sources else "compiles"
            findings.problems.append(
                f"{target.name} {what} files that are generated or outside the project, such as "
                f"{target.foreign[0]}; cxy builds only the project's files, and needs other "
                "code as Conan packages"
            )
            if not target.sources:
                continue
        if "C" in target.languages:
            findings.problems.append(f"{target.name} compiles C, which cxy cannot build yet")
        targets.append(target)

    default = Project(root, Manifest(name=name, version=version))
    tests = [t for t in targets if t.kind == "EXECUTABLE" and _is_test(default, t)]
    libraries = [t for t in targets if t.kind in _LIBRARIES and t.sources]
    programs = [t for t in targets if t.kind == "EXECUTABLE" and t not in tests]
    library = _only(libraries, "libraries", "one library", findings)
    program = _only(programs, "programs", "one application", findings)
    if library is not None and library.kind not in ("STATIC_LIBRARY", "SHARED_LIBRARY"):
        findings.warnings.append(f"cxy builds {library.name} as a static library")

    layout = _layout(library, program, tests, targets, default.manifest.layout, findings)
    std = _standard(cmake, [*filter(None, [library, program]), *tests], findings)
    if program is not None and program.file_name:
        built = PurePosixPath(program.file_name).stem
        if built != name:
            findings.warnings.append(f"cxy names the program {name}, not {built}")
    if cmake.options:
        listed = ", ".join(f"{k}={v}" for k, v in sorted(cmake.options.items()))
        findings.warnings.append(
            f"CMake options are not used; cxy builds as with these values: {listed}"
        )

    document = _document(name, version, std, recipe, layout)
    try:
        manifest = manifests.parse(tomlkit.dumps(document))
    except CxyError as error:
        raise CxyError(f"the proposed {manifests.FILENAME} is invalid: {error}") from error
    project = Project(root, manifest)
    try:
        sources = project.sources()
    except CxyError as error:
        findings.problems.append(str(error))
        return tomlkit.dumps(document)

    separate = _compare(project, sources, library, program, tests, findings)
    if separate:
        document["test"] = {"separate": True}
    _check_frameworks(project, sources, document, findings)
    objects = any(found.sources for found in sources.programs)
    _carry_settings(project, library, program, tests, objects, document, findings)
    _check_inherited([t for t in targets if t.sources], provided or Provided(), findings)
    text = tomlkit.dumps(document)
    try:
        manifests.parse(text)
    except CxyError as error:
        raise CxyError(f"the proposed {manifests.FILENAME} is invalid: {error}") from error
    return text


def _package_name(text: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    name = re.sub(r"^[^a-z]+", "", name)
    try:
        manifests.check_name(name)
    except CxyError:
        return "project"
    return name


def _is_test(project: Project, target: Target) -> bool:
    patterns = project.manifest.layout.test_patterns
    return any(
        fnmatchcase(PurePosixPath(source).name, pattern)
        for source in target.sources
        for pattern in patterns
    ) or any(testing.detect(project, source) for source in target.sources)


def _only(targets: Sequence[Target], plural: str, limit: str, findings: Findings) -> Target | None:
    if len(targets) > 1:
        findings.problems.append(
            f"the project builds {len(targets)} {plural} "
            f"({', '.join(t.name for t in targets)}), but cxy builds {limit}"
        )
    return targets[0] if targets else None


def _common(paths: Iterable[str]) -> str | None:
    parents = [str(PurePosixPath(path).parent) for path in paths]
    if not parents:
        return None
    return PurePosixPath(os.path.commonpath(parents)).as_posix() if len(parents) > 1 else parents[0]


def _layout(
    library: Target | None,
    program: Target | None,
    tests: Sequence[Target],
    targets: Sequence[Target],
    default: Layout,
    findings: Findings,
) -> dict[str, str]:
    """Return the layout settings that differ from cxy's defaults."""
    layout: dict[str, str] = {}
    lib_dir = _common(library.sources) if library else None
    if lib_dir is not None and lib_dir != default.lib:
        layout["lib"] = lib_dir

    src_dir = None
    if program is not None:
        mains = [s for s in program.sources if PurePosixPath(s).stem == "main"]
        if len(mains) == 1:
            src_dir = str(PurePosixPath(mains[0]).parent)
            if src_dir != default.src:
                layout["src"] = src_dir
        else:
            findings.problems.append(
                f"cxy needs the application's main() in a file named main.cpp, but "
                f"{program.name} is built from {', '.join(program.sources)}"
            )

    beside = [
        source
        for target in tests
        for source in target.sources
        if any(fnmatchcase(PurePosixPath(source).name, p) for p in default.test_patterns)
    ]
    others = [
        source
        for target in tests
        for source in target.sources
        if source not in beside and (library is None or source not in library.sources)
    ]
    tests_dir = _common(others)
    if tests_dir is not None and tests_dir not in (default.tests, "."):
        layout["tests"] = tests_dir

    own_dirs = {d for d in (lib_dir, src_dir, tests_dir) if d is not None}
    candidates = list(
        dict.fromkeys(
            include
            for target in (library, program, *tests, *targets)
            if target is not None
            for include in target.includes
            if include not in own_dirs
        )
    )
    include_dir = candidates[0] if candidates else lib_dir if library else None
    if len(candidates) > 1:
        findings.problems.append(
            f"the project uses several include directories ({', '.join(candidates)}), but "
            f"cxy uses one: {candidates[0]}"
        )
    if include_dir is not None and include_dir != default.include:
        layout["include"] = include_dir

    return layout


def _standard(cmake: CMakeProject, targets: Sequence[Target], findings: Findings) -> int:
    standards = [t.standard for t in targets if t.standard is not None]
    if not standards:
        findings.warnings.append(
            "the project does not choose a C++ standard, so package.std is 20; set the one it needs"
        )
        return 20
    std = max(standards)
    explicit = cmake.mentions_standard or any(t.standard_is_explicit for t in targets)
    if not explicit and cmake.toolchain_standard == std:
        findings.warnings.append(
            f"the project does not choose a C++ standard, so package.std is {std}, from your "
            "Conan profile; set the one it needs"
        )
    if std not in STANDARDS:
        newer = min((s for s in STANDARDS if s >= std), default=STANDARDS[-1])
        findings.problems.append(f"cxy cannot build C++{std}, so package.std is {newer}")
        return newer
    return std


def _document(
    name: str, version: str, std: int, recipe: Recipe, layout: Mapping[str, str]
) -> tomlkit.TOMLDocument:
    document = tomlkit.document()
    document["package"] = {"name": name, "version": version, "std": std}
    for section, test in (("dependencies", False), ("dev-dependencies", True)):
        table = tomlkit.table()
        for requirement in recipe.requirements:
            if requirement.test == test:
                table[requirement.name] = _requirement(requirement)
        document[section] = table
    if recipe.overrides:
        document["overrides"] = dict(recipe.overrides)
    if layout:
        document["layout"] = dict(layout)
    return document


def _requirement(requirement: Requirement) -> Any:
    if not requirement.force and not requirement.options:
        return requirement.version
    value = tomlkit.inline_table()
    value["version"] = requirement.version
    if requirement.force:
        value["force"] = True
    if requirement.options:
        options = tomlkit.inline_table()
        options.update(requirement.options)
        value["options"] = options
    return value


def _compare(
    project: Project,
    sources: Sources,
    library: Target | None,
    program: Target | None,
    tests: Sequence[Target],
    findings: Findings,
) -> bool:
    """Report files that cxy would build differently, and return whether tests are separate."""
    tests_dir = project.manifest.layout.tests
    built = {*(library.sources if library else ()), *(program.sources if program else ())}
    ours = {*sources.compiled, *(found.main for found in sources.programs if found.main)}
    _differences(built, ours, "the library and application", findings.problems)

    tested = {source for target in tests for source in target.sources} - built
    _differences(tested, {*sources.unit_tests, *sources.tests}, "the tests", findings.warnings)

    bundled = [t for t in tests if len([s for s in t.sources if s in sources.unit_tests]) > 1]
    for target in bundled:
        findings.warnings.append(
            f"cxy builds each test beside the code as its own program; CMake builds "
            f"{target.name} from several"
        )

    in_tests = [s for s in sources.tests if testing.is_test(project, s)]
    if len(in_tests) < 2:
        return False
    counts = [
        len([s for s in target.sources if s in in_tests])
        for target in tests
        if any(s in in_tests for s in target.sources)
    ]
    if counts and all(count == 1 for count in counts):
        return True
    if counts != [len(in_tests)]:
        findings.warnings.append(
            f"CMake groups the tests in {tests_dir}/ into programs in a way cxy cannot; cxy "
            "builds them as one program (or one each, with separate = true in [test])"
        )
    return False


def _differences(theirs: set[str], ours: set[str], what: str, into: list[str]) -> None:
    if missing := sorted(theirs - ours):
        into.append(f"cxy would not compile these files of {what}: {', '.join(missing)}")
    if extra := sorted(ours - theirs):
        into.append(
            f"cxy would also compile these files as part of {what}, which CMake does not: "
            f"{', '.join(extra)}"
        )


def _check_frameworks(
    project: Project,
    sources: Sources,
    document: tomlkit.TOMLDocument,
    findings: Findings,
) -> None:
    used = set().union(*(testing.detect(project, s) for s in (*sources.unit_tests, *sources.tests)))
    dependencies: Any = document["dependencies"]
    dev: Any = document["dev-dependencies"]
    for framework in sorted(used):
        if framework in dev:
            continue
        if framework in dependencies:
            dev[framework] = dependencies[framework]
            del dependencies[framework]
            findings.warnings.append(
                f"{framework} is a test framework, so it is in [dev-dependencies]"
            )
        else:
            findings.problems.append(
                f"the tests use {framework}, which the recipe does not require"
            )
    try:
        manifest = manifests.parse(tomlkit.dumps(document))
        testing.plan(Project(project.root, manifest), sources)
    except CxyError as error:
        findings.problems.append(str(error))


def _carry_settings(
    project: Project,
    library: Target | None,
    program: Target | None,
    tests: Sequence[Target],
    objects: bool,
    document: tomlkit.TOMLDocument,
    findings: Findings,
) -> None:
    """Carry the definitions and options that the project sets into the manifest.

    cxy gives every target the [build] settings, and passes a library's definitions on to
    whatever links it: the program and the tests. The program's reach the tests that link
    its objects. ``objects`` is whether there are any.
    """
    everything = [t for t in (library, program, *tests) if t is not None]
    if not everything:
        return
    for target in everything:
        if target.link_flags:
            listed = " ".join(target.link_flags)
            findings.problems.append(
                f"{target.name} is linked with options cxy cannot set: {listed}"
            )
    usable = {d for t in everything for d in t.definitions if manifests.is_definition(d)}
    for definition in sorted({d for t in everything for d in t.definitions} - usable):
        findings.problems.append(f"cxy cannot set -D{definition}, which is not NAME or NAME=value")

    def common(values: Sequence[tuple[str, ...]]) -> list[str]:
        return [v for v in values[0] if all(v in other for other in values[1:])]

    build_defines = [d for d in common([t.definitions for t in everything]) if d in usable]
    build_flags = common([t.flags for t in everything])
    lib_defines, lib_flags, bin_defines, bin_flags = [], [], [], []
    if library is not None:
        lib_defines = [d for d in library.definitions if d in usable and d not in build_defines]
        lib_flags = [f for f in library.flags if f not in build_flags]
    if program is not None:
        bin_defines = [d for d in program.definitions if d in usable and d not in build_defines]
        bin_flags = [f for f in program.flags if f not in build_flags]

    dropped: dict[tuple[str, bool], list[str]] = {}
    for target in everything:
        given = {*build_defines, *lib_defines}
        flags = set(build_flags)
        if target is library:
            flags |= set(lib_flags)
        if target is program or (target in tests and objects):
            given |= set(bin_defines)
        if target is program:
            flags |= set(bin_flags)
        files = _compiled_files(project, target.sources)

        def uses(definition: str, files: Mapping[str, str] = files) -> bool:
            macro = re.split(r"[=(]", definition, maxsplit=1)[0]
            pattern = re.compile(rf"\b{re.escape(macro)}\b")
            return any(pattern.search(text) for text in files.values())

        for definition in sorted(given - set(target.definitions)):
            if uses(definition):
                findings.problems.append(
                    f"cxy would also compile {target.name} with -D{definition}, which it uses"
                )
        for definition in target.definitions:
            if definition in usable and definition not in given:
                dropped.setdefault((definition, uses(definition)), []).append(target.name)
        if extra := [f for f in target.flags if f not in flags]:
            findings.problems.append(
                f"{target.name} is compiled with options cxy cannot set for it: {' '.join(extra)}"
            )
    for (definition, used), names in dropped.items():
        where = names[0] if len(names) == 1 else f"{len(names)} targets ({_some(names)})"
        verb = "is" if len(names) == 1 else "are"
        message = f"{where} {verb} compiled with -D{definition}, which cxy cannot set for them"
        if used:
            findings.problems.append(message)
        else:
            findings.warnings.append(f"{message}; nothing they compile uses it, so it is left out")

    for section, defines, flags, extra in (
        ("build", build_defines, build_flags, {}),
        ("lib", lib_defines, lib_flags, {"shared": True} if _is_shared(library) else {}),
        ("bin", bin_defines, bin_flags, {}),
    ):
        table = {**extra, **({"defines": defines} if defines else {})}
        if flags:
            table["flags"] = flags
        if table:
            document[section] = table


def _is_shared(library: Target | None) -> bool:
    return library is not None and library.kind == "SHARED_LIBRARY"


def _check_inherited(targets: Sequence[Target], provided: Provided, findings: Findings) -> None:
    """Report what targets get from somewhere other than Conan, such as CMake's find modules."""
    groups: dict[tuple[str, ...], list[str]] = {}
    for target in targets:
        items = tuple(item for item in target.inherited if not provided.provides(item))
        if items:
            groups.setdefault(items, []).append(target.name)
    for items, names in groups.items():
        where = names[0] if len(names) == 1 else f"{len(names)} targets ({_some(names)})"
        verb = "is" if len(names) == 1 else "are"
        findings.problems.append(
            f"{where} {verb} built with {' '.join(items)}, which come from something other than "
            "a Conan package, such as a CMake find module; cxy builds only with Conan packages"
        )


def _some(names: Sequence[str], limit: int = 3) -> str:
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown}, ..."


def _compiled_files(project: Project, sources: Iterable[str]) -> dict[str, str]:
    """Return the project files compiled with these sources, with their text."""
    found: dict[str, str] = {}
    pending = list(sources)
    while pending:
        current = pending.pop()
        if current in found:
            continue
        path = project.root / current
        if not path.is_file():
            continue
        found[current] = path.read_text(encoding="utf-8", errors="replace")
        pending += closure.included_files(project, current)
    return found
