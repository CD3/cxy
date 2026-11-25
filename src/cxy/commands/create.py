from pathlib import Path

from cxy.core import scaffold


def new(path: Path, *, name: str | None = None, lib: bool = False) -> None:
    """Create a new project in a new directory.

    Parameters
    ----------
    path
        Directory to create.
    name
        Package name; defaults to the directory name.
    lib
        Create a library without an application.
    """
    scaffold.new(path, name=name, lib_only=lib)


def init(*, name: str | None = None, lib: bool = False) -> None:
    """Create a project in the current directory, keeping existing files.

    Parameters
    ----------
    name
        Package name; defaults to the directory name.
    lib
        Create a library without an application.
    """
    scaffold.init(Path.cwd(), name=name, lib_only=lib)
