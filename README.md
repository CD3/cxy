# cxy

A cargo-like project tool for C++. It reads `cxy.toml`, generates the Conan and CMake
files, and drives Conan, CMake and Ninja, skipping steps whose inputs have not changed.

```sh
cxy new hello
cd hello
cxy run
cxy test -- "[some-tag]"   # arguments after -- go to the program
```

Commands: `new`, `init`, `build`, `run`, `test`, `add`, `remove`, `fmt`, `lint`, `lock`, `export`, `clean`.

## cxy.toml

```toml
[package]
name = "hello"
version = "0.1.0"
std = 20

[dependencies]
spdlog = "1.14.1"
boost = { version = "1.86.0", options = { shared = false } }
# Win a version conflict for a direct dependency:
zlib = { version = "1.3.1", force = true }
# Replace the detected CMake names:
openssl = { version = "3.3.2", cmake = { package = "OpenSSL", targets = ["OpenSSL::SSL"] } }

[dev-dependencies]
catch2 = "3.7.1"

[overrides]            # versions of transitive dependencies
fmt = "10.2.0"

[tools]                # optional exact versions of formatters and linters, run with uv
clang-format = "18.1.8"

[layout]               # defaults
lib = "lib"
src = "src"
include = "include"
tests = "tests"
build = "build"
test-patterns = ["*_test.cpp", "*.test.cpp"]
```

- `src/main.cpp` is the application. Other `src/` files are shared with the tests.
- A source matching `test-patterns` beside the code it tests becomes its own test program.
- Everything in `tests/` goes into one test program.
- Layout directories may coincide, for example `lib = "src"` and `include = "src"`.

## Dependencies

```sh
cxy add fmt                          # the newest release
cxy add fmt@11.0.2 spdlog            # fmt/11.0.2 works too
cxy add --dev trompeloeil            # only for tests
cxy add boost -o shared=false        # with a Conan option
cxy add fmt@10.2.0 --force-version   # win a version conflict
cxy add --override zlib@1.3.1        # version of an indirect dependency
cxy remove fmt                       # from whichever section it is in
```

These edit `cxy.toml`, keeping its comments, and update `cxy.lock`. If the dependencies cannot be
resolved, or a package does not have an option, nothing changes. Overriding a package that nothing depends on gives a warning.

## Locking

`cxy.lock` records the exact version and revision of every dependency. Building creates and
updates it, and keeps locked versions until you upgrade them. Commit it.

```sh
cxy lock                         # update it without building
cxy lock --upgrade               # newest versions that cxy.toml allows
cxy lock --upgrade-package fmt   # ...of one package
cxy lock --check                 # fail if it is out of date
cxy build --locked               # ...and likewise for other commands, for use in CI
```

Dependencies can differ between platforms, and a lock covers only those it was made on. Run
`cxy lock` on each: it adds what is missing. Changing dependencies in `cxy.toml` removes entries
that are not needed on the current platform.

## Testing

```sh
cxy test                           # everything
cxy test --test http               # only lib/net/http_test.cpp
cxy test --test http --isolated    # ...without compiling unrelated sources
```

Test programs normally link the whole library. `--isolated` instead compiles only the sources
a test needs, so you can port code one file at a time after a breaking change. It finds them by
following includes, and assumes that `http.hpp` is implemented in `http.cpp`.

## Formatting

```sh
cxy fmt                       # the whole project
cxy fmt lib CMakeLists.txt    # some of it
cxy fmt --check               # change nothing; fail if anything needs formatting
cxy fmt --diff                # ...and show what would change
```

| Files | Tool | Configuration |
|---|---|---|
| C++ | clang-format | `.clang-format` |
| `CMakeLists.txt`, `*.cmake` | gersemi | `.gersemirc` |
| `conanfile.py` | ruff | `ruff.toml` or `pyproject.toml` |

In a project that does not use cxy, this covers every file in the git repository that is not
ignored. Without git, it covers the current directory. A tool with no configuration uses its
defaults, and cxy says so.

## Linting

```sh
cxy lint                      # the whole project; fails if there are any problems
cxy lint lib CMakeLists.txt   # some of it
cxy lint --fix                # fix what the tools can, then report the rest
cxy lint --deep               # add slower static analysis
```

| Files | Tool | Configuration |
|---|---|---|
| C++ | clang-tidy | `.clang-tidy` |
| C++, with `--deep` | cppcheck | |
| `CMakeLists.txt`, `*.cmake` | cmake-lint, if installed | `.cmake-format.yaml` and others |
| `conanfile.py` | ruff | `ruff.toml` or `pyproject.toml` |

clang-tidy and cppcheck are not installed with cxy: install them or pin them in `[tools]`. The
versions of cppcheck and cmake-lint to pin are those of the Python packages `cppcheck` and
`cmakelang`.

The C++ tools need `compile_commands.json`. cxy configures its own projects to get one. It never
configures other projects: it searches the usual build directories, or use `--build-dir`. If there
is none the C++ files are not checked, and the command fails.

Project configuration can run code: cmake-lint reads `.cmake-format.py`, and gersemi can load
extensions. Be careful when checking a project that you do not trust.

## Sharing a project with people who do not use cxy

`cxy export` writes `conanfile.py`, `CMakeLists.txt` and `conan.lock` to the project root. It replaces
files that it wrote, but needs `--force` for any that have been edited or were there before. To build the result:

```sh
conan install . --build=missing -s compiler.cppstd=20
cmake --preset conan-release
cmake --build --preset conan-release
ctest --preset conan-release
```

Conan uses `conan.lock` automatically. On a platform it does not cover, add `--lockfile-partial`.
After editing dependencies in an exported `conanfile.py`, delete `conan.lock` or make a new one:
overrides recorded in it take precedence over the recipe's.

Source files are listed explicitly, so export again after adding or removing any.

## Tools

Set a variable such as `CXY_CONAN` or `CXY_CLANG_FORMAT` to choose a specific tool. Otherwise a
version pinned in `[tools]` is used, then `PATH`, then the copies installed with cxy.

## Development

```sh
uv run poe check          # lint, format-check, typecheck and test
uv run poe lint           # ruff
uv run poe format-check   # ruff; fix with "uv run ruff format"
uv run poe typecheck      # ty
uv run poe test           # unit tests
uv run poe test -m e2e    # real builds; set CONAN_HOME to isolate the Conan cache
```

## License

MIT. See `LICENSE`.
