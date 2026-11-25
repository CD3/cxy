"""The cxy.lock file: Conan's lockfile, with the manifest settings it was made from.

Conan's lockfile alone is not enough. It records overrides, and they take precedence over the
recipe's, even in strict mode. Recording the settings lets cxy notice that they have changed.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cxy.core.errors import CxyError
from cxy.core.manifest import Dependency, Manifest
from cxy.core.stamp import write_if_changed

FILENAME = "cxy.lock"
_VERSION = 1
_SECTIONS = ("requires", "build_requires", "python_requires", "config_requires")


@dataclass(frozen=True)
class Lock:
    inputs: Mapping[str, Any]
    conan: Mapping[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Lock | None":
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version, lock = data["version"], cls(data["inputs"], data["conan"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CxyError(
                f"cannot read {path}: {error}; fix it, or delete it to lock again"
            ) from error
        if version != _VERSION:
            raise CxyError(f"{path} was made by a different version of cxy")
        return lock

    def save(self, path: Path) -> bool:
        data = {"version": _VERSION, "inputs": self.inputs, "conan": self.conan}
        return write_if_changed(path, json.dumps(data, indent=4) + "\n")

    def matches(self, manifest: Manifest) -> bool:
        return self.inputs == inputs(manifest)

    def conan_text(self) -> str:
        return json.dumps(self.conan, indent=4) + "\n"

    def packages(self) -> dict[str, set[str]]:
        """Return the locked versions of each package."""
        result: dict[str, set[str]] = {}
        for section in _SECTIONS:
            for ref in self.conan.get(section, []):
                name, _, version = ref.partition("#")[0].partition("/")
                result.setdefault(name, set()).add(version)
        return result

    def without_overrides(self) -> "Lock":
        """Forget overrides, so that Conan takes them from the recipe."""
        conan = {key: value for key, value in self.conan.items() if key != "overrides"}
        return Lock(self.inputs, conan)

    def without_package(self, name: str) -> "Lock":
        if name not in self.packages():
            raise CxyError(f"{name} is not in {FILENAME}")
        conan = dict(self.conan)
        for section in _SECTIONS:
            if section in conan:
                conan[section] = [r for r in conan[section] if r.partition("/")[0] != name]
        return Lock(self.inputs, conan)


def inputs(manifest: Manifest) -> dict[str, Any]:
    """Return the manifest settings that determine which packages are needed."""
    return {
        "dependencies": _describe(manifest.dependencies),
        "dev-dependencies": _describe(manifest.dev_dependencies),
        "overrides": dict(sorted(manifest.overrides.items())),
    }


def _describe(dependencies: tuple[Dependency, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dependency in sorted(dependencies, key=lambda dependency: dependency.name):
        entry: dict[str, Any] = {"version": dependency.version}
        if dependency.force:
            entry["force"] = True
        if dependency.options:
            entry["options"] = dict(sorted(dependency.options.items()))
        result[dependency.name] = entry
    return result


def changes(before: Lock | None, after: Lock) -> list[str]:
    """Describe what changed between two locks."""
    old = before.packages() if before else {}
    new = after.packages()
    lines = []
    for name in sorted(old.keys() | new.keys()):
        was, now = (" ".join(sorted(p.get(name, ()))) for p in (old, new))
        if was == now:
            continue
        if not was:
            lines.append(f"added {name} {now}")
        elif not now:
            lines.append(f"removed {name} {was}")
        else:
            lines.append(f"changed {name} {was} -> {now}")
    return lines
