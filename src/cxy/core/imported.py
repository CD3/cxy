"""The cxy-import.json file: what ``cxy import`` last proposed, and what it read to propose it.

``cxy import --update`` merges a new proposal using the last one as the base, so that it can tell
the user's changes from the project's. The hashes of what it read let builds notice that the
project's CMake or Conan files have changed since.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cxy.core.errors import CxyError
from cxy.core.stamp import write_if_changed

FILENAME = "cxy-import.json"
_VERSION = 1


@dataclass(frozen=True)
class Record:
    proposed: str
    """The text of the cxy.toml that the import proposed, before any edits."""

    inputs: Mapping[str, str]
    """The hash of each file it read, by its path relative to the project root."""

    @classmethod
    def load(cls, root: Path) -> "Record | None":
        path = root / FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version, record = data["version"], cls(data["proposed"], dict(data["inputs"]))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CxyError(f"cannot read {path}: {error}; delete it to import again") from error
        if version != _VERSION:
            raise CxyError(f"{path} was made by a different version of cxy")
        return record

    @classmethod
    def of(cls, root: Path, proposed: str, inputs: Iterable[str]) -> "Record":
        return cls(proposed, {name: _hash(root / name) for name in sorted(set(inputs))})

    def save(self, root: Path) -> bool:
        data = {"version": _VERSION, "proposed": self.proposed, "inputs": dict(self.inputs)}
        return write_if_changed(root / FILENAME, json.dumps(data, indent=2) + "\n")

    def changed(self, root: Path) -> list[str]:
        """The files read by the import that have changed or gone since."""
        return [name for name, digest in self.inputs.items() if _hash(root / name) != digest]


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
