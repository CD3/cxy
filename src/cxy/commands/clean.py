from cxy.core import pipeline
from cxy.core.project import Project


def clean() -> None:
    """Delete the build directory."""
    pipeline.clean(Project.find())
