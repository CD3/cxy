from typing import Annotated

from cyclopts import Parameter

from cxy.commands.build import Options
from cxy.core import linting
from cxy.core.workspace import Workspace


def lint(
    *paths: str,
    fix: Annotated[bool, Parameter(negative="")] = False,
    deep: Annotated[bool, Parameter(negative="")] = False,
    build_dir: str | None = None,
    options: Options | None = None,
) -> int:
    """Find problems in C++, CMake and Conan files. Fails if there are any.

    This also works in projects that do not use cxy.

    Parameters
    ----------
    paths
        Files or directories to check; defaults to the whole project.
    fix
        Apply the tools' automatic fixes, then report what remains.
    deep
        Also run slower static analysis.
    build_dir
        Directory containing compile_commands.json. A cxy project configures its own; in
        others the default is to search the usual places.
    """
    return linting.lint(
        Workspace.find(),
        paths,
        fix=fix,
        deep=deep,
        build_dir=build_dir,
        options=(options or Options()).to_core(),
    )
