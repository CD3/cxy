from pathlib import Path

from cxy.core.project import HEADER_SUFFIXES, SOURCE_SUFFIXES

_CPP_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES | {".cppm", ".inl", ".ipp", ".ixx", ".tpp"}


def is_cpp(path: Path) -> bool:
    return path.suffix in _CPP_SUFFIXES


def is_cpp_header(path: Path) -> bool:
    return path.suffix in HEADER_SUFFIXES


def is_cpp_source(path: Path) -> bool:
    return path.suffix in SOURCE_SUFFIXES


def is_cmake(path: Path) -> bool:
    return path.name == "CMakeLists.txt" or path.suffix == ".cmake"


def is_conanfile(path: Path) -> bool:
    return path.name == "conanfile.py"
