"""Add and remove dependencies in cxy.toml, keeping its comments and layout."""

import copy
import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import tomlkit

from cxy.core import lock as locks
from cxy.core import manifest as manifests
from cxy.core import pipeline, tools
from cxy.core.errors import CxyError
from cxy.core.lock import Lock
from cxy.core.manifest import OptionValue
from cxy.core.pipeline import BuildOptions
from cxy.core.project import Project

DEPENDENCIES = "dependencies"
DEV_DEPENDENCIES = "dev-dependencies"
OVERRIDES = "overrides"
SECTIONS = (DEPENDENCIES, DEV_DEPENDENCIES, OVERRIDES)

# Ordinary releases, as opposed to pre-releases, variants and dated snapshots.
_RELEASE = re.compile(r"\d+(\.\d+)*[a-z]?")


@dataclass(frozen=True)
class Request:
    name: str
    version: str | None = None
    options: Mapping[str, OptionValue] = field(default_factory=dict)
    force: bool = False


def parse_request(text: str, options: Sequence[str] = (), *, force: bool = False) -> Request:
    """Parse "fmt", "fmt@11.0.2" or Conan's "fmt/11.0.2"."""
    match = re.fullmatch(r"([^@/\s]+)(?:[@/](.+))?", text)
    if match is None:
        raise CxyError(f"cannot understand {text!r}; use name, name@version or name/version")
    values: dict[str, OptionValue] = {}
    for option in options:
        key, separator, value = option.partition("=")
        if not key or not separator:
            raise CxyError(f"cannot understand option {option!r}; use name=value")
        values[key] = {"true": True, "false": False}.get(value.lower(), value)
    return Request(match[1], match[2], values, force)


def add(
    project: Project,
    requests: Sequence[Request],
    options: BuildOptions,
    section: str = DEPENDENCIES,
) -> None:
    resolved = []
    for request in requests:
        manifests.check_dependency_name(request.name)
        version = request.version or newest(project, request.name)
        resolved.append(Request(request.name, version, request.options, request.force))
    text = _read(project)
    for request in resolved:
        text = add_to(text, section, request)
    _apply(project, text, options)
    for request in resolved:
        tools.status("Added", f"{request.name} {request.version} to {section}")
    if section == OVERRIDES:
        _warn_if_unused(project, [request.name for request in resolved])


def remove(project: Project, names: Sequence[str], options: BuildOptions) -> None:
    text = _read(project)
    removed = []
    for name in names:
        section = find_section(tomllib.loads(text), name)
        text = remove_from(text, name)
        removed.append((name, section))
    _apply(project, text, options)
    for name, section in removed:
        tools.status("Removed", f"{name} from {section}")


def find_section(data: Mapping[str, Any], name: str) -> str | None:
    return next((section for section in SECTIONS if name in data.get(section, {})), None)


def add_to(text: str, section: str, request: Request) -> str:
    """Return the manifest text with a dependency added or updated."""
    data = tomllib.loads(text)
    current = find_section(data, request.name)
    if current not in (None, section):
        raise CxyError(_wrong_section(request.name, current, section))
    if section == OVERRIDES and (request.options or request.force):
        raise CxyError("an override is only a version; it cannot have options or be forced")
    old = data.get(section, {}).get(request.name)
    table: dict[str, Any] = copy.deepcopy(old) if isinstance(old, dict) else {}
    table["version"] = request.version
    if request.options:
        table["options"] = {**table.get("options", {}), **request.options}
    if request.force:
        table["force"] = True
    value = request.version if not isinstance(old, dict) and len(table) == 1 else table
    expected = copy.deepcopy(data)
    expected.setdefault(section, {})[request.name] = value

    document: Any = tomlkit.parse(text)
    if section not in document:
        document[section] = tomlkit.table()
    if isinstance(old, dict):
        # Change only what is different, to keep the entry's comments and layout.
        entry = document[section][request.name]
        for key, item in table.items():
            if old.get(key) != item:
                entry[key] = _inline(item)
    else:
        document[section][request.name] = _inline(value)
    return _checked(document, expected)


def _wrong_section(name: str, current: str | None, wanted: str) -> str:
    if wanted == OVERRIDES:
        return (
            f"{name} is already in {current}, so it cannot be overridden; "
            "add it there with --force-version instead"
        )
    if current == OVERRIDES:
        return (
            f"{name} is in {OVERRIDES}, which is only for indirect dependencies; "
            "remove it first, or use --override to change its version"
        )
    return f"{name} is already in {current}; remove it first"


def _warn_if_unused(project: Project, names: Sequence[str]) -> None:
    """Point out overrides of packages that nothing depends on, which may be misspelled."""
    lock = Lock.load(project.root / locks.FILENAME)
    packages = lock.packages() if lock else {}
    for name in names:
        if name not in packages:
            tools.status("Warning", f"nothing depends on {name}, so overriding it has no effect")


def remove_from(text: str, name: str) -> str:
    """Return the manifest text with a dependency removed."""
    data = tomllib.loads(text)
    section = find_section(data, name)
    if section is None:
        raise CxyError(f"{name} is not in {', '.join(SECTIONS[:-1])} or {SECTIONS[-1]}")
    expected = copy.deepcopy(data)
    del expected[section][name]
    document: Any = tomlkit.parse(text)
    del document[section][name]
    return _checked(document, expected)


def newest(project: Project, name: str) -> str:
    """Return the newest release of a package that Conan knows about."""
    output = tools.run(
        "conan",
        "list",
        f"{name}/*",
        "--remote=*",
        "--cache",
        "--format=json",
        "-verror",
        cwd=project.root,
        capture=True,
    )
    try:
        found = json.loads(output)
        sources = {source: refs for source, refs in found.items() if "error" not in refs}
        errors = [source for source in found if source not in sources]
        versions = [ref.partition("/")[2] for refs in sources.values() for ref in refs]
    except (ValueError, AttributeError, TypeError) as error:
        raise CxyError("could not understand Conan's JSON output") from error
    for source in errors:
        tools.status("Warning", f"could not reach Conan remote {source}")
    version = choose(versions)
    if version is None:
        where = " in the Conan remotes that could be reached" if errors else ""
        raise CxyError(f"no package called {name}{where}")
    return version


def choose(versions: Sequence[str]) -> str | None:
    """Choose the newest release, or if there are none, the newest of any kind."""
    releases = [version for version in versions if _RELEASE.fullmatch(version)]
    return max(releases or versions, key=_order, default=None)


def _order(version: str) -> list[tuple[int, int, str]]:
    """Order versions by their parts, numerically where possible: 1.1.1 < 1.1.1w < 1.1.2."""
    return [
        (1, int(part), "") if part.isdigit() else (0, 0, part)
        for part in re.findall(r"\d+|[a-z]+", version.lower())
    ]


def _apply(project: Project, text: str, options: BuildOptions) -> None:
    """Write the manifest and lock it, restoring it if that fails."""
    path = project.root / manifests.FILENAME
    original = path.read_bytes()
    manifests.parse(text)
    path.write_bytes(text.encode())
    try:
        pipeline.lock(Project.find(project.root), options)
    except BaseException:
        path.write_bytes(original)
        tools.status("Restored", f"{manifests.FILENAME}, because locking failed")
        raise


def _read(project: Project) -> str:
    # Not read_text, which would convert line endings.
    return (project.root / manifests.FILENAME).read_bytes().decode()


def _inline(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    table = tomlkit.inline_table()
    for key, item in value.items():
        table[key] = _inline(item)
    return table


def _checked(document: Any, expected: Mapping[str, Any]) -> str:
    text = tomlkit.dumps(document)
    if _without_empty(tomllib.loads(text)) != _without_empty(expected):
        raise CxyError(f"could not edit {manifests.FILENAME} safely; please edit it by hand")
    return text


def _without_empty(data: Mapping[str, Any]) -> dict[str, Any]:
    """Drop empty sections, which mean the same as missing ones."""
    return {key: value for key, value in data.items() if value or key not in SECTIONS}
