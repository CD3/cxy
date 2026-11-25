from typing import Annotated

from cyclopts import Parameter

from cxy.commands.build import CommonOptions
from cxy.core import pipeline
from cxy.core.project import Project


def export(
    *,
    force: Annotated[bool, Parameter(negative="")] = False,
    options: CommonOptions | None = None,
) -> None:
    """Write Conan and CMake files for building without cxy.

    Parameters
    ----------
    force
        Replace existing files.
    """
    pipeline.export(Project.find(), (options or CommonOptions()).to_core(), force=force)
