from typing import Annotated

from cyclopts import Parameter

from cxy.commands.build import ResolveOptions
from cxy.core import pipeline
from cxy.core.project import Project


def lock(
    *,
    check: Annotated[bool, Parameter(negative="")] = False,
    upgrade: Annotated[bool, Parameter(negative="")] = False,
    upgrade_packages: Annotated[
        tuple[str, ...], Parameter(name=["--upgrade-package", "-P"], negative="")
    ] = (),
    options: ResolveOptions | None = None,
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
    """
    pipeline.lock(
        Project.find(),
        (options or ResolveOptions()).to_core(),
        check=check,
        upgrade=upgrade,
        upgrade_packages=upgrade_packages,
    )
