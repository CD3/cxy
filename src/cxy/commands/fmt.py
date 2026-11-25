from typing import Annotated

from cyclopts import Parameter

from cxy.core import formatting
from cxy.core.workspace import Workspace


def fmt(
    *paths: str,
    check: Annotated[bool, Parameter(negative="")] = False,
    diff: Annotated[bool, Parameter(negative="")] = False,
) -> int:
    """Format C++, CMake and Conan files.

    This also works in projects that do not use cxy.

    Parameters
    ----------
    paths
        Files or directories to format; defaults to the whole project.
    check
        List files that need formatting without changing them. Fails if there are any.
    diff
        Show what would change without changing it. Fails if anything would.
    """
    return formatting.fmt(Workspace.find(), paths, check=check, diff=diff)
