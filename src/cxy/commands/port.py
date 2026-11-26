from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from cxy.core import porting
from cxy.core.pipeline import BuildOptions


def port(
    *,
    dry_run: Annotated[bool, Parameter(negative="")] = False,
    force: Annotated[bool, Parameter(negative="")] = False,
    profile: str = "default",
    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"], negative="")] = False,
) -> None:
    """Write a cxy.toml for the CMake and Conan project in the current directory.

    Nothing else in the project changes. Conan and CMake run in cxy's build directory to
    find out what the project builds, and anything cxy would build differently is reported.

    Parameters
    ----------
    dry_run
        Print the cxy.toml instead of writing it.
    force
        Write cxy.toml even if cxy would build the project differently.
    profile
        Conan profile for the host and build contexts.
    verbose
        Show full tool output.
    """
    porting.port(
        Path.cwd(),
        BuildOptions(profile=profile, verbose=verbose),
        dry_run=dry_run,
        force=force,
    )
