"""Find the files to check, in cxy projects and any others."""

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cxy.core import manifest as manifests
from cxy.core.errors import CxyError
from cxy.core.project import Project


@dataclass(frozen=True)
class Workspace:
    root: Path
    project: Project | None = None
    """Set only for projects managed by cxy."""

    @classmethod
    def find(cls, start: Path | None = None) -> "Workspace":
        """Use the nearest directory with a cxy.toml or git repository, else the start."""
        start = (start or Path.cwd()).resolve()
        for directory in (start, *start.parents):
            if (directory / manifests.FILENAME).is_file():
                return cls(directory, Project.find(directory))
            if (directory / ".git").exists():
                return cls(directory)
        return cls(start)

    @property
    def pins(self) -> Mapping[str, str]:
        return self.project.manifest.tools if self.project else {}

    def files(self, paths: Sequence[str] = ()) -> list[Path]:
        """Return the files in the workspace, or just those under the given paths."""
        if not paths:
            return self.project.files() if self.project else self._list(self.root)
        found: set[Path] = set()
        for name in paths:
            path = Path(os.path.abspath(name))
            if path.is_dir():
                found.update(self._list(path))
            elif path.is_file():
                found.add(path)
            else:
                raise CxyError(f"{name} does not exist")
        return sorted(found)

    def _list(self, directory: Path) -> list[Path]:
        files = _git_files(directory)
        if files is None:
            files = _walk(directory)
        build = self.project.build_dir.parent if self.project else None
        return sorted(
            path
            for path in set(files)
            # Git lists deleted files and submodules. Symlinks may lead out of the tree.
            if path.is_file()
            and not path.is_symlink()
            and not (build and path.is_relative_to(build))
        )


def _git_files(directory: Path) -> list[Path] | None:
    """List files that git does not ignore, or return None outside a repository."""
    git = shutil.which("git")
    if git is None:
        return None
    result = subprocess.run(
        [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=directory,
        capture_output=True,
    )
    if result.returncode:
        return None
    return [directory / os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def _walk(directory: Path) -> list[Path]:
    found = []
    for current, directories, names in os.walk(directory):
        here = Path(current)
        # Without git to say what is generated, skip hidden and CMake build directories.
        directories[:] = [
            d
            for d in directories
            if not d.startswith(".") and not (here / d / "CMakeCache.txt").exists()
        ]
        found += [here / name for name in names]
    return found
