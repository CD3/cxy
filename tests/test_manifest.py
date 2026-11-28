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
        ('[build]\ndefines = ["1X"]\n', "NAME or NAME=value"),
        ('[build]\ndefines = "X"\n', "list of strings"),
        ('[lib]\nflags = ["-DX"]\n', "lib.defines"),
        ('[bin]\nflags = ["/DX"]\n', "bin.defines"),
        ("[bin]\nshared = true\n", "unknown key 'shared'"),
        ('[lib]\nshared = "yes"\n', "true or false"),
        ("[lib.core]\n", "lib.core.sources is required"),
        ('[lib]\nshared = true\n[lib.core]\nsources = "core"\n', "not both"),
        ('[lib.Core]\nsources = "core"\n', "invalid name lib.Core"),
        ('[lib.core]\nsources = "core"\nuses = ["io"]\n', "no library called 'io'"),
        ('[lib.core]\nsources = "core"\nuses = ["core"]\n', "cannot use itself"),
        (
            '[lib.a]\nsources = "a"\nuses = ["b"]\n[lib.b]\nsources = "b"\nuses = ["a"]\n',
            "cycle: a -> b -> a",
        ),
        ('[lib.a]\nsources = "src"\n[lib.b]\nsources = "src"\n', "same sources directory"),
        ('[lib.x]\nsources = "x"\n[bin.x_lib]\nmain = "x.cpp"\n', "target x_lib"),
        ("[bin.tool]\n", "needs main"),
        ('[bin.tool]\nmain = "tool.hpp"\n', "must be a C"),
        ('[bin.tool]\nmain = "tool.cpp"\nuses = ["nope"]\n', "no library called 'nope'"),
        ('[lib.core]\nsources = "build/core"\n', "inside layout.build"),
    ],
)
def test_errors(extra, message):
    with pytest.raises(CxyError, match=message):
        parse(PACKAGE + extra)


def test_components():
    manifest = parse(
        PACKAGE
        + """
[lib.core]
sources = "src/core"
[lib.io]
sources = "src/io"
include = "include"
uses = ["core"]
shared = true
[bin.scatter]
sources = "apps/scatter"
defines = ["X"]
[bin.convert]
main = "apps/convert.cpp"
uses = ["io"]
"""
    )
    core, io, scatter, convert = manifest.components
    assert (core.directory, core.include, core.uses) == ("src/core", "src/core", ())
    assert (io.include, io.uses, io.shared) == ("include", ("core",), True)
    assert (scatter.main, scatter.uses) == ("apps/scatter/main", ("core", "io"))
    assert scatter.settings.defines == ("X",)
    assert (convert.directory, convert.main, convert.uses) == (None, "apps/convert.cpp", ("io",))
    assert manifest.include_directories == (
        "src/core",
        "include",
        "src/core",
        "src/io",
        "apps/scatter",
    )


def test_named_programs_keep_the_default_library():
    manifest = parse(PACKAGE + '[bin.tool]\nmain = "tools/tool.cpp"\n')
    library, tool = manifest.components
    assert (library.name, library.directory, library.include) == ("app", "lib", "include")
    assert tool.uses == ("app",)


def test_settings():
    manifest = parse(
        PACKAGE
        + """
[build]
defines = ["X", 'Y="a b"']
[lib]
shared = true
flags = ["-O2"]
[bin]
defines = ["Z=1"]
"""
    )
    library, program = manifest.components
    assert manifest.build.defines == ("X", 'Y="a b"')
    assert library.shared and library.settings.flags == ("-O2",)
    assert program.settings.defines == ("Z=1",) and not program.shared


@pytest.mark.parametrize("name", ["test", "My App", "1app", "a;b"])
def test_invalid_names(name):
    with pytest.raises(CxyError, match="invalid package name"):
        parse(f'[package]\nname = "{name}"\nversion = "1"\n')


WHEN = """
[build]
defines = ["COMMON"]
flags = ["-g"]
[lib.core]
sources = "core"
defines = ["CORE"]
[bin.app]
main = "app.cpp"
[when.windows.build]
defines = ["NOMINMAX", "COMMON"]
[when.msvc.build]
flags = ["/W4"]
[when.'any(gcc, clang)'.build]
flags = ["-Wall", "-g"]
[when.'all(windows, msvc)'.lib.core]
defines = ["CORE_WINAPI"]
[when.linux.bin.app]
flags = ["-pthread"]
"""


def test_conditional_settings():
    manifest = parse(PACKAGE + WHEN)
    assert [c.where for c in manifest.when] == [
        "when.windows",
        "when.msvc",
        "when.'any(gcc, clang)'",
        "when.'all(windows, msvc)'",
        "when.linux",
    ]
    windows = manifest.resolve({"os": "Windows", "compiler": "msvc", "build_type": "Debug"})
    assert windows.when == ()
    assert windows.build.defines == ("COMMON", "NOMINMAX")
    assert windows.build.flags == ("-g", "/W4")
    assert windows.libraries[0].settings.defines == ("CORE", "CORE_WINAPI")
    assert windows.programs[0].settings.flags == ()
    linux = manifest.resolve({"os": "Linux", "compiler": "gcc", "build_type": "Debug"})
    # Repeated flags are kept, since some must be.
    assert linux.build.flags == ("-g", "-Wall", "-g")
    assert linux.libraries[0].settings.defines == ("CORE",)
    assert linux.programs[0].settings.flags == ("-pthread",)

    [core, app] = manifest.components
    assert [(c.where, s.defines) for c, s in manifest.conditional(core)] == [
        ("when.'all(windows, msvc)'", ("CORE_WINAPI",))
    ]
    assert [c.where for c, _ in manifest.conditional(app)] == ["when.linux"]


def test_conditional_settings_of_the_layout():
    text = PACKAGE + '[when.windows.lib]\ndefines = ["A"]\n[when.windows.bin]\nflags = ["/W4"]\n'
    manifest = parse(text)
    resolved = manifest.resolve({"os": "Windows"})
    [library, program] = resolved.components
    assert library.settings.defines == ("A",) and program.settings.flags == ("/W4",)
    [library, program] = manifest.components
    assert [s.defines for _, s in manifest.conditional(library)] == [("A",)]
    assert [s.flags for _, s in manifest.conditional(program)] == [("/W4",)]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("[when]\nwindows = 1\n", "when.windows must be a table"),
        ("[when.windows]\nflags = []\n", "unknown key 'flags' in \\[when.windows\\]"),
        ("[when.windos.build]\n", "did you mean 'windows'"),
        ("[when.windows.build]\nshared = true\n", "unknown key 'shared'"),
        ("[when.windows.lib]\nshared = true\n", "unknown key 'shared'"),
        ('[when.windows.build]\ndefines = ["-DX"]\n', "not NAME or NAME=value"),
        ('[when.windows.lib.core]\ndefines = ["X"]\n', "there is no \\[lib.core\\]"),
        (
            '[lib.core]\nsources = "core"\n[when.windows.lib]\ndefines = ["X"]\n',
            "use \\[when.windows.lib.NAME\\]",
        ),
        (
            '[lib.core]\nsources = "core"\n[when.windows.lib.core]\nuses = ["core"]\n',
            "unknown key 'uses' in \\[when.windows.lib.core\\]",
        ),
        ("[when.'all(windows, linux)'.build]\n", "when.'all\\(windows, linux\\)'.*never be true"),
    ],
)
def test_conditional_errors(extra, message):
    with pytest.raises(CxyError, match=message):
        parse(PACKAGE + extra)
