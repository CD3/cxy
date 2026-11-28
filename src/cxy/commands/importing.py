import sys
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from cxy.core import importing, merging
from cxy.core.pipeline import BuildOptions


def import_(
    *,
    update: Annotated[bool, Parameter(negative="")] = False,
    resolve: Literal["yours", "upstream"] | None = None,
    dry_run: Annotated[bool, Parameter(negative="")] = False,
    force: Annotated[bool, Parameter(negative="")] = False,
    profile: str = "default",
    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"], negative="")] = False,
) -> None:
    """Write a cxy.toml from the CMake and Conan project in the current directory.

    Nothing else in the project changes, apart from cxy-import.json, which records what was
    imported. Conan and CMake run in cxy's build directory to find out what the project builds,
    and anything cxy would build differently is reported.

    Parameters
    ----------
    update
        Merge the project's changes since the last import into the existing cxy.toml. A setting
        changed both there and in the project is a conflict, and you are asked which to use.
    resolve
        Decide every conflict without asking: keep yours, or take upstream's from the project.
    dry_run
        Print the cxy.toml instead of writing it; with --update, the changes and conflicts.
    force
        Write cxy.toml even if cxy would build the project differently.
    profile
        Conan profile for the host and build contexts.
    verbose
        Show full tool output.
    """
    importing.import_project(
        Path.cwd(),
        BuildOptions(profile=profile, verbose=verbose),
        dry_run=dry_run,
        force=force,
        update=update,
        resolve=resolve,
        choose=_ask if sys.stdin.isatty() else None,
    )


def _ask(conflict: merging.Conflict) -> bool:
    print(
        "\n".join(["Conflict in " + merging.explain(conflict)[0], *merging.explain(conflict)[1:]])
    )
    while True:
        answer = input("Use yours or upstream's? [y/u] ").strip().lower()
        if answer in ("y", "yours"):
            return False
        if answer in ("u", "upstream"):
            return True
