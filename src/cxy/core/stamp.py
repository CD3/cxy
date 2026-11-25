"""Skip work whose inputs have not changed."""

import hashlib
import json
from pathlib import Path


def write_if_changed(path: Path, text: str) -> bool:
    """Write a file only if its content differs, preserving its timestamp otherwise."""
    if path.is_file() and path.read_bytes() == text.encode():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return True


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Stamp:
    """Records a fingerprint of the inputs to a completed stage."""

    def __init__(self, path: Path, *inputs: object) -> None:
        self._path = path
        self._key = hashlib.sha256(json.dumps(inputs).encode()).hexdigest()

    def is_current(self, *outputs: Path) -> bool:
        return (
            self._path.is_file()
            and self._path.read_text(encoding="utf-8") == self._key
            and all(output.exists() for output in outputs)
        )

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._key, encoding="utf-8")
