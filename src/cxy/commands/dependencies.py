from typing import Annotated

from cyclopts import Parameter

from cxy.commands.build import ResolveOptions
from cxy.core import dependencies
from cxy.core.errors import CxyError
from cxy.core.project import Project


def add(
    *packages: str,
    dev: Annotated[bool, Parameter(negative="")] = False,
    override: Annotated[bool, Parameter(negative="")] = False,
    package_options: Annotated[
        tuple[str, ...], Parameter(name=["--option", "-o"], negative="")
    ] = (),
    force_version: Annotated[bool, Parameter(negative="")] = False,
    options: ResolveOptions | None = None,
) -> None:
    """Add dependencies to cxy.toml, or change their versions.

    Parameters
    ----------
    packages
        Packages such as ``fmt`` for the newest version, or ``fmt@11.0.2``.
    dev
        Add to dev-dependencies, which only the tests use.
    override
        Set the version of indirect dependencies, whatever asks for them.
    package_options
        A Conan option for the package, such as ``shared=true``. May be repeated.
    force_version
        Use this version even if other dependencies ask for a different one.
    """
    if not packages:
        raise CxyError("name at least one package to add")
    if package_options and len(packages) > 1:
        raise CxyError("--option needs exactly one package")
    if dev and override:
        raise CxyError("--dev and --override cannot be combined")
    section = dependencies.DEPENDENCIES
    if dev:
        section = dependencies.DEV_DEPENDENCIES
    elif override:
        section = dependencies.OVERRIDES
    requests = [
        dependencies.parse_request(package, package_options, force=force_version)
        for package in packages
    ]
    core = (options or ResolveOptions()).to_core()
    dependencies.add(Project.find(), requests, core, section)


def remove(*packages: str, options: ResolveOptions | None = None) -> None:
    """Remove dependencies and overrides from cxy.toml.

    Parameters
    ----------
    packages
        Names of the packages.
    """
    if not packages:
        raise CxyError("name at least one package to remove")
    core = (options or ResolveOptions()).to_core()
    dependencies.remove(Project.find(), packages, core)
