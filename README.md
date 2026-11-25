# cxy

A cargo-like project tool for C++. It reads `cxy.toml`, generates the Conan and CMake
files, and drives Conan, CMake and Ninja, skipping steps whose inputs have not changed.

```sh
cxy new hello
cd hello
cxy run
cxy test -- "[some-tag]"   # arguments after -- go to the program
```

Commands: `new`, `init`, `build`, `run`, `test`, `export`, `clean`.
Planned: `add`, `remove`, `lock`.

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

## Testing

```sh
cxy test                           # everything
cxy test --test http               # only lib/net/http_test.cpp
cxy test --test http --isolated    # ...without compiling unrelated sources
```

Test programs normally link the whole library. `--isolated` instead compiles only the sources
a test needs, so you can port code one file at a time after a breaking change. It finds them by
following includes, and assumes that `http.hpp` is implemented in `http.cpp`.

## Sharing a project with people who do not use cxy

`cxy export` writes `conanfile.py` and `CMakeLists.txt` to the project root. It will not replace
files with different content unless given `--force`. To build the result:

```sh
conan install . --build=missing -s compiler.cppstd=20
cmake --preset conan-release
cmake --build --preset conan-release
ctest --preset conan-release
```

Source files are listed explicitly, so export again after adding or removing any.

## Tools

Set `CXY_CONAN`, `CXY_CMAKE` or `CXY_NINJA` to choose a specific tool. Otherwise `PATH` is
searched first, then the copies installed with cxy.

## Development

```sh
uv run pytest            # fast tests
uv run pytest -m e2e     # real builds; set CONAN_HOME to isolate the Conan cache
uv run ruff check && uv run ruff format --check && uv run ty check
```
