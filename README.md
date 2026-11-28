# cxy

A cargo-like project tool for C++. Rather than reinventing the wheel, `cxy` uses the
tools that people are already using to manage dependencies, configure builds, run
builds, format and check code. It even supports exporting projects to "standard"
Conan/CMake format so you can share it with people that don't want to use it.

## Basic Usage

`cxy` reads `cxy.toml`, generates Conan and CMake
files, and drives Conan, CMake and Ninja. It also tracks the inputs for each step
and will skip steps whose inputs have not changed to reduce the edit-compile-run cycle time..

```sh
cxy new hello
cd hello
cxy run
cxy test -- "[some-tag]"   # arguments after -- go to the program
cxy build -D TRACE=1       # a definition for this build only, as if added to [build]
```

Commands: `new`, `init`, `port`, `build`, `run`, `test`, `add`, `remove`, `fmt`, `lint`, `lock`, `export`, `clean`.


## Why?

Because going from an idea for a library or application to a project that
compiles is a pain and going from a project that compiles to a project that uses
best practices is hard.

Tools exist to do everything that is needed, they just have to be setup. Conan does a
good job managing dependencies and CMake is the defacto build configuration standard
whether we like it or not. Ninja is a fast build executor, and `clang-tools` can format and check code. `cxy` 
just brings these tools together in a simple, easy to use tool.

### Why Python?

Because I like Python and it's already a requirement to use Conan. We don't really have to worry about
performance because most of the work is going to be in dependency resolution and compiling. `cxy` just
needs to write some configuration files and call commands. Python works great for that.

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

[dev-dependencies]     # only for tests
catch2 = "3.7.1"       # or gtest, or both

[test]                 # optional
framework = "catch2"   # for tests that do not show which framework they use
separate = false       # whether each test file in tests/ is its own program
working-directory = "." # where tests run; by default, the build directory

[overrides]            # versions of transitive dependencies
fmt = "10.2.0"

[tools]                # optional exact versions of formatters and linters, run with uv
clang-format = "18.1.8"

[build]                # optional, for everything compiled, tests included
defines = ["USE_SIMD", 'LOG_LEVEL="info"']
flags = ["-Wall"]      # passed to the compiler as they are

[lib]                  # optional, for the library
shared = true          # build a shared library; by default it is static
defines = ["HAVE_HDF5"]
flags = ["-O3"]

[bin]                  # optional, for the application
defines = ["APP_NAME=\"hello\""]

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
- Everything in `tests/` goes into one test program for each test framework, unless `separate` is
  set.
- Layout directories may coincide, for example `lib = "src"` and `include = "src"`.
- Definitions in `[lib]` reach whatever uses the library, since its headers may depend on them:
  the application and the tests. Those in `[bin]` reach the tests that use the application's
  code. Flags in `[lib]` and `[bin]` apply only to their own sources.
- Definitions are `NAME` or `NAME=value`, without `-D`. Flags are not portable between compilers.
- On Windows, a shared library exports all its functions, so that it needs no export macros, but
  not its global variables.

### Several libraries and programs

Named `[lib.NAME]` and `[bin.NAME]` tables replace the library and program that `[layout]`
describes, so one kind can be named while `[layout]` still describes the other:

```toml
[lib.core]
sources = "src/core"       # its sources and private headers
include = "include"        # its public headers; by default, the sources directory
shared = true              # also defines and flags, as in [lib]

[lib.io]
sources = "src/io"
uses = ["core"]            # libraries it links, which pass on their definitions

[bin.scatter]
sources = "apps/scatter"   # main.cpp here is the main file

[bin.convert]
main = "tools/convert.cpp" # a program may be only its main file
uses = ["io"]              # by default, every library
```

- The deepest directory decides where a file belongs, as with `[layout]`.
- Tests link every library and the code of every program.
- `cxy run --bin NAME` chooses the program to run, if there are several.
- A static library that a shared one uses is compiled as position-independent code.
- In exported files, `BUILD_SHARED_LIBS` and Conan's `shared` option choose the kind of library
  only if all libraries are alike. Otherwise each keeps its own kind.

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
cxy test --filter timeout          # only tests with this in their names
```

Test programs normally link the whole library. `--isolated` instead compiles only the sources
a test needs, so you can port code one file at a time after a breaking change. It finds them by
following includes, and assumes that `http.hpp` is implemented in `http.cpp`.

### Test frameworks

Catch2 (version 3) and gtest are supported. New projects use Catch2. To use gtest, add it:

```sh
cxy add --dev gtest
cxy remove catch2      # unless you want both
```

Either framework supplies `main()`, but your tests can define their own instead. gmock is linked
too, if the gtest package was built with it.

With both frameworks, for example while moving from one to the other, each test uses the one that
it includes, directly or through the project's headers:

- A test beside a source uses its own framework.
- `tests/` becomes two programs, such as `hello_tests_catch2` and `hello_tests_gtest`. Files that
  include neither framework are compiled into both.
- One file cannot include both. Each framework has a `main()`, and the linker might silently choose
  one, so that the other's tests never run.

If cxy cannot tell which framework some tests use, it stops and asks you to set `framework` in the
`[test]` section.

### Separate programs in `tests/`

```toml
[test]
separate = true
```

This makes each test file in `tests/` its own program, as tests beside sources always are. Use it if
files reuse the same test names, which is common in projects that were built that way, or to build
one file at a time. Linking takes longer, as there are more programs.

A test file is one that matches `test-patterns` or uses a macro such as `TEST` or `TEST_CASE`. The
others in `tests/` are helpers, such as a custom `main()`, and are compiled into every program.

### Working directory

Tests run in the build directory, such as `build/cxy/debug`, wherever you call `cxy test` from, so
that files they write stay out of the source tree. If tests read files by relative path, choose a
directory relative to the project root:

```toml
[test]
working-directory = "tests/data"
```

Relative paths in arguments to the tests, such as for a report, are relative to that directory too.
`ctest` uses the same directory, including in exported projects: `ctest --test-dir build/cxy/debug`
works after building.

### Choosing tests

`--test` takes the name of a test program. For a test with its own program, it also takes the
test's path or name, or the name of the source it tests. `tests` means everything in `tests/`. If
that is one program, use `--filter` to run only some of it. `--test` may be repeated.

`--filter` works with either framework. It ignores case with Catch2 but not with gtest. It need not
match something in every program, but `cxy test` fails if it matches nothing at all, which gtest
alone would call a success.

Arguments after `--` go to the test programs unchanged, so they depend on the framework:

```sh
cxy test -- "[some-tag]"                  # Catch2
cxy test -- --gtest_filter='Suite.*'      # gtest
```

If the tests use both, choose some with `--test` first: the frameworks take different arguments.

### CTest

`cxy test` runs the test programs directly. `cxy test --ctest` runs them with CTest instead, which
starts a process for each test case. That is slower, but a crash loses only that test, and CTest has
features such as timeouts and running failed tests again:

```sh
cxy test --ctest                          # --test and --filter work too
cxy test --ctest --jobs 8                 # run tests in parallel
cxy test --ctest -- --rerun-failed        # arguments after -- go to CTest
cxy test --ctest -- --timeout 10
```

Tests run one at a time unless you use `--jobs`: those that write the same file could interfere with
each other. `--filter` considers case here, whichever the framework.

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

## Using cxy in an existing project

In a CMake project that uses Conan, `cxy port` writes a `cxy.toml` that builds the same thing, so
that you can use cxy while everyone else keeps their own setup. It writes nothing else in the
project: it runs Conan and CMake on the project in `build/cxy/port`, and reads what CMake reports
about each target with its File API. The File API leaves out interface libraries, so a file that
`port` has CMake include after `project()` lists them. `cxy port --dry-run` prints the manifest
instead.

It finds the dependencies, their options and overrides, the C++ standard, which directories hold
the libraries, programs, headers and tests, which libraries each uses, the test framework, and the definitions and compiler
options that the project sets, which go in `[build]`, `[lib]` and `[bin]`. An interface library
with headers in the project is a header-only library, with the definitions it passes on. Anything that cxy would build
differently is a problem, and then `cxy.toml` is written only with `--force`:

- an application whose `main()` is not in a file named `main.cpp`, or, with several programs, a
  program whose main file cannot be found;
- libraries or programs that cxy cannot tell apart, such as two in one directory;
- files that one of them compiles and cxy would not, or the reverse;
- compile definitions and options that cxy cannot give the same targets, such as one only for the
  tests, or a library's definition that the application does not get but uses;
- link options;
- what an interface library passes on that cxy cannot: compiler options, generator expressions
  other than `$<BUILD_INTERFACE:…>` and `$<INSTALL_INTERFACE:…>`, and definitions from one with
  no headers in the project;
- flags and libraries from somewhere other than Conan, such as CMake's `FindOpenMP`;
- tool requirements other than CMake and Ninja, and options for packages that are not direct
  dependencies.

Warnings are for what to check, such as a C++ standard that only comes from your Conan profile. A
recipe or `CMakeLists.txt` can run any code, so only port projects that you trust.

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

If the project has a compiled library, the recipe has a `shared` option, which defaults to
`lib.shared`, so `-o "&:shared=True"` builds it as a shared library. Without Conan, use
`-DBUILD_SHARED_LIBS=ON`. Conan sets `BUILD_SHARED_LIBS` only in a new build directory, so use
another one, or delete it, to change this.

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
