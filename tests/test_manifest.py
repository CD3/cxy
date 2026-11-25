import pytest

from cxy.core.errors import CxyError
from cxy.core.manifest import parse

PACKAGE = '[package]\nname = "app"\nversion = "1.0"\n'


def test_dependencies_and_overrides():
    manifest = parse(
        PACKAGE
        + """
[dependencies]
fmt = "11.0.2"
boost = { version = "1.86.0", options = { shared = false }, force = true }
[overrides]
zlib = "1.3.1"
"""
    )
    fmt, boost = manifest.dependencies
    assert fmt.ref == "fmt/11.0.2"
    assert boost.force and boost.options == {"shared": False}
    assert manifest.overrides == {"zlib": "1.3.1"}


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("[depndencies]\n", "unknown key"),
        ('[dependencies]\nfmt = "1"\n[overrides]\nfmt = "2"\n', "force = true"),
        ('[dependencies]\nfmt = "1"\n[dev-dependencies]\nfmt = "1"\n', "both"),
        ('[layout]\nlib = "../lib"\n', "inside the project"),
        ('[layout]\nbuild = "."\n', "cannot be the project"),
        ('[layout]\nbuild = "src"\n', "cannot be inside"),
        ("[dependencies]\nfmt = 1\n", "version string"),
    ],
)
def test_errors(extra, message):
    with pytest.raises(CxyError, match=message):
        parse(PACKAGE + extra)


@pytest.mark.parametrize("name", ["test", "My App", "1app", "a;b"])
def test_invalid_names(name):
    with pytest.raises(CxyError, match="invalid package name"):
        parse(f'[package]\nname = "{name}"\nversion = "1"\n')
