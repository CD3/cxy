from typing import Annotated

from cyclopts import Parameter

from cxy.core import pipeline
from cxy.core.project import Project


def lock(
    *,
    check: Annotated[bool, Parameter(negative="")] = False,
    upgrade: Annotated[bool, Parameter(negative="")] = False,
    upgrade_packages: Annotated[
        tuple[str, ...], Parameter(name=["--upgrade-package", "-P"], negative="")
    ] = (),
    profiles: Annotated[tuple[str, ...], Parameter(name=["--profile"], negative="")] = (),
    release: Annotated[bool, Parameter(name=["--release", "-r"], negative="")] = False,
    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"], negative="")] = False,
) -> None:
    """Record the exact dependency versions to use in cxy.lock.

    Building does this too. Versions that are already locked stay the same unless upgraded.

    Parameters
    ----------
    check
        Change nothing; fail if cxy.lock is missing or out of date.
    upgrade
        Choose the newest allowed versions of everything.
    upgrade_packages
        Choose the newest allowed version of this package. May be repeated.
    profiles
        Conan profile for the host and build contexts; by default, "default". May be
        repeated, to lock for other platforms too, such as one for Windows with MSVC.
    release
        Lock for a build with optimizations.
    verbose
        Show full tool output.
    """
    pipeline.lock(
        Project.find(),
        pipeline.BuildOptions(release=release, verbose=verbose),
        check=check,
        upgrade=upgrade,
        upgrade_packages=upgrade_packages,
        profiles=profiles,
    )
