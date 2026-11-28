# Design: conditional settings

Status: phases 1 and 2 are implemented, with the decisions at the end. Phases 3 and 4 are
proposed. Phase 1 differs from the proposal in these ways:

- Conditions can test only `os`, `compiler` and `build_type`, the settings that the exported
  CMake can test too. `arch` and the others need more thought, and the `msvc-like` condition and
  version comparisons can be added later without changing the syntax.
- Flags are added as they are, without dropping repeats, since some must be repeated, as in
  `-Xclang A -Xclang B`. Repeated definitions are dropped.
- Managed builds write a `CMakeLists.txt` for each build type, since a condition on the build
  type makes them differ. With one file, Debug and Release builds reconfigured each other.

Phase 2 differs in these ways:

- `link-flags` is only for `[build]` and programs. A library's own link options matter only
  for a shared library, and passing them on would give the tests a program's options, such as
  `/SUBSYSTEM:WINDOWS`. System libraries reach whatever links the library, and a program's
  reach the tests that link its objects.
- In the exported CMake, a conditional dependency is found inside an `if()` block, and linked
  with a generator expression, such as `$<$<PLATFORM_ID:Windows>:WIL::WIL>`, so that every
  place that links dependencies works unchanged.
- `cxy.lock` records the CMake names of conditional dependencies only, so that the lock of a
  project without conditions doesn't change. Builds record them, since `conan lock create` and
  `conan graph info` don't give them.
- A conditional test framework is an error, since every platform runs the tests.
- `conan lock create` with a Windows MSVC profile works on Linux, and Conan ignores
  `default_options` for packages that aren't in the graph: both were checked.

## Problem

A real project needs settings that differ by platform, compiler or build type: `NOMINMAX` on
Windows, `/W4` with MSVC but `-Wall` with GCC and Clang, a `TRACE` define in Debug builds, a
Windows-only dependency or system library. `cxy.toml` has no way to say any of this, so such a
project can't be described, built or exported.

## Summary

One new top-level table, `[when.'<condition>'.<section>]`, reuses the sections that exist
today: `build`, `lib`, `lib.NAME`, `bin`, `bin.NAME`, and later `dependencies`. A condition is
a small expression over the Conan host settings.

- **Managed builds:** cxy resolves the conditions in Python against the profile, then writes
  plain CMake and a plain conanfile.
- **`cxy export`:** conditions become `if()` blocks and `$<CONFIG:…>` in CMake, and
  `self.settings` checks in the conanfile.
- **Import:** it doesn't invent conditions in the first phases. It keeps the ones you wrote,
  which the merge already does.

## Which settings can be conditional

| Setting | Phase | Notes |
|---|---|---|
| `defines`, `flags` in `[build]`, `[lib]`, `[bin]`, `[lib.NAME]`, `[bin.NAME]` | 1 | Lists, so they append. |
| New `link-flags` and `system-libs` (such as `ws2_32`, `bcrypt`, `dl`) | 2 | New keys, useful unconditionally too. Import reports link options as problems today. |
| `[dependencies]`, `[dev-dependencies]` and their options | 2 | Touches the lock and export's CMake names. |
| `shared` | 3 | A single value, so it replaces. It changes position-independent code, `WINDOWS_EXPORT_ALL_SYMBOLS` and `shared_default()`. |
| Sources (`win32/*.cpp`) | 3 | Affects `Project.sources()`, test planning, `--isolated` and lint. Sketch: `extra-sources = ["src/core/win32"]` in a `[when]` component table claims that directory only when the condition matches. |
| `[test]`, `[overrides]`, `[layout]`, `uses`, `include`, `main` | not planned | Structural: varying them makes the project's shape differ by platform, which nobody has asked for. |

## Conditions

A condition is evaluated against the host settings of the Conan profile in use, after cxy's
own `-s build_type` and `-s compiler.cppstd`. In a managed build that happens at each build; in
an exported project, at the other user's configure and install.

- OS shorthands: `windows`, `linux`, `macos`, and `unix` (any OS but Windows).
- Compiler shorthands: `msvc`, `gcc`, `clang`, `apple-clang`. `clang` doesn't match
  `apple-clang`, as Conan and CMake (`Clang` vs `AppleClang`) both keep them apart.
- `key = "value"` for any Conan setting: `os`, `compiler`, `build_type`, `arch`,
  `compiler.runtime`, and so on.
- `all(…)`, `any(…)` and `not(…)` combine them.

Compiler conditions suit flags (`-Wall` vs `/W4`); OS conditions suit defines and system
libraries.

## Syntax

```toml
[build]
defines = ["USE_SIMD"]

[when.windows.build]
defines = ["WIN32_LEAN_AND_MEAN", "NOMINMAX"]

[when.msvc.build]
flags = ["/W4", "/permissive-"]

[when.'any(gcc, clang, apple-clang)'.build]
flags = ["-Wall", "-Wextra"]

[when.'build_type = "Debug"'.build]
defines = ["TRACE"]

[when.'all(windows, msvc)'.lib.core]
defines = ["CORE_USE_WINAPI"]

# Phase 2
[when.windows.dependencies]
wil = "1.0.240803.1"

[when.windows.lib.io]
system-libs = ["ws2_32"]
```

How the parts combine:

- **Lists append:** the unconditional value first, then each matching `[when]` table in file
  order, with duplicates dropped.
- **Single values replace** (`shared`, in phase 3). Two tables that can match at once must not
  set one differently.
- **Component tables:** `[when.X.lib.core]` must name an existing `[lib.core]`. `[when.X.lib]`
  follows the same settings-or-components rule as `[lib]`.
- **Keys:** phase 1 allows only `defines` and `flags`.

### Alternatives considered

- **Nesting in existing sections**, such as `[build.windows]` or `[lib.core.msvc]`. cxy reads
  every table under `[lib]` as a component, so `[lib.windows]` is a library named `windows`.
  Avoiding that means reserving names, and `all(windows, msvc)` would need an order
  (`[build.windows.msvc]` or `[build.msvc.windows]`).
- **Arrays of tables** (`[[build.when]]` with an `if` key) **or conditions inside lists**
  (`flags = ["-Wall", { value = "/W4", if = "msvc" }]`). The `import --update` merge treats an
  array as one value, so editing one entry would conflict with any upstream change. They also
  mix types in lists.
- **Cargo's `[target.'cfg(…)']`.** In CMake, and in cxy's own words, a target is a library or
  program, which `[lib.core]` already names. `[target.core]` would read as a component.

## Generation

### Managed builds

Conditions are resolved in Python, so that `shared` and sources, which Python decides, never
disagree with defines and flags, which CMake receives.

1. `host_settings(project, options)` runs `conan profile show` for the profile, build type and
   standard, and caches the host settings in `build/cxy/<type>/`. The cache is stamped with the
   profile, Conan's configuration and Conan's version, so Conan runs once more only when one of
   those changes.
2. `Manifest.resolve(settings)` returns a manifest without conditions, which configuring,
   installing, testing, running and linting use.
3. The existing stamps then stay correct: a different profile resolves to different CMake and
   conanfile text, which reconfigures.
4. Known gaps:
   - profiles that read environment variables through Jinja, which `conan.stamp` already misses;
   - a `CXX` that differs from the profile's compiler. Exported CMake follows
     `CMAKE_CXX_COMPILER_ID`, while managed builds follow the profile. cxy could read the
     compiler ID from `CMakeCache.txt` after configuring and warn when they don't match.
5. Before any tool runs, the manifest is validated statically (see Validation), in place of
   today's early `cmakelists(…, {})` check.

### Export

Export uses the unresolved manifest.

| Conan | CMake `if()` | Generator expression |
|---|---|---|
| `os=Windows` | `WIN32` | `$<PLATFORM_ID:Windows>` |
| `os=Linux` | `CMAKE_SYSTEM_NAME STREQUAL "Linux"` | `$<PLATFORM_ID:Linux>` |
| `os=Macos` | `CMAKE_SYSTEM_NAME STREQUAL "Darwin"` | `$<PLATFORM_ID:Darwin>` |
| `unix` | `UNIX` | — |
| `compiler=msvc` | `CMAKE_CXX_COMPILER_ID STREQUAL "MSVC"` | `$<CXX_COMPILER_ID:MSVC>` |
| `gcc` / `clang` / `apple-clang` | `GNU` / `Clang` / `AppleClang` | `$<CXX_COMPILER_ID:…>` |
| `build_type=X` | none, because of multi-config generators | `$<CONFIG:X>` |
| `arch` | `CMAKE_SYSTEM_PROCESSOR` is unreliable | deferred |

- The compiler ID is used rather than the `MSVC` variable, which is also true for clang-cl
  (which Conan calls `clang`).
- OS and compiler conditions become `if()` blocks, since people read the exported file and
  `find_package` needs `if()` anyway. A condition that mentions `build_type` becomes a generator
  expression.
- `cmake.quote()` escapes `$`, so values inside a generator expression need their own escaping
  (`$<COMMA>`, `$<ANGLE-R>`, `$<SEMICOLON>`).

```cmake
if(WIN32)
  add_compile_definitions("WIN32_LEAN_AND_MEAN" "NOMINMAX")
  find_package(wil REQUIRED CONFIG)
endif()
if(CMAKE_CXX_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$")
  add_compile_options("-Wall" "-Wextra")
endif()
add_compile_definitions("$<$<CONFIG:Debug>:TRACE>")
```

In the conanfile (phase 2):

```python
    def requirements(self):
        self.requires("spdlog/1.14.1")
        if self.settings.os == "Windows":
            self.requires("wil/1.0.240803.1")
```

Options of conditional dependencies can stay in `default_options` as `wil/*:x` if Conan ignores
patterns for packages that aren't in the graph, which needs checking. Options that differ by
platform need `configure()`.

## Import

- Import stays single-configuration. Reading `if(WIN32)` from CMake files means parsing CMake
  code, which the design notes rule out, and configuring for other operating systems can't
  work.
- Conditions you write by hand survive `--update`: a setting only in your file has no base and
  no upstream, so it counts as your change. If upstream renames `lib.core`, a leftover
  `[when.X.lib.core]` fails the post-merge check, whose error should name the `[when]` table.
- Moving a value conflicts. If you move `/W4` from `[build]` into `[when.msvc.build]` and
  upstream then adds a flag, `build.flags` conflicts. Phase 4 can remove, from upstream's and
  the base's unconditional lists, what your `[when]` tables supply under that configuration.
- `cxy-import.json` should record the import's OS and compiler, so that `--update` on another
  configuration can warn that every platform difference will look like a project change.
- Generator expressions in compiled targets reach the File API already evaluated, so
  `$<$<CXX_COMPILER_ID:MSVC>:/W4>` vanishes on GCC without a report. The import hook could read
  the raw `COMPILE_OPTIONS`, `COMPILE_DEFINITIONS` and `LINK_OPTIONS`, as it does for interface
  libraries, and turn `$<$<CXX_COMPILER_ID:X>:v>`, `$<$<PLATFORM_ID:X>:v>`,
  `$<$<CONFIG:X>:v>` and `$<$<AND:…>:v>` into `[when]` tables. Anything else stays an
  unreadable problem. That reads property text, not CMake code. Phase 4.

## Validation

At parse time:

- An unknown shorthand or setting is an error, with a suggestion.
- Values are checked against a built-in list of Conan's settings.
- The settings the conditions mention have few values, so cxy can try every combination
  (excluding impossible ones, such as MSVC off Windows or Apple Clang off macOS). That finds:
  - a condition that can never match, such as `all(windows, apple-clang)`: an error;
  - two tables that can match at once and set a single value or a dependency's version
    differently: an error;
  - a dependency that is both unconditional and conditional: an error.

## Lock file

Conan 2 lockfiles aren't tied to one profile, so entries for several platforms can live
together.

1. `lock.inputs()` must describe the unresolved manifest. Otherwise switching platforms looks
   like a dependency change and cleans the other platforms' entries. A manifest without
   conditions keeps the same inputs.
2. `cxy lock --profile A --profile B` could lock every platform from one machine, if
   `conan lock create` works with a Windows profile on Linux, which needs checking.
3. `cxy lock --check` covers only the platform it runs on.
4. Export can't learn the CMake names of a dependency that isn't in the graph on its host.
   `cxy.lock` could record the names each lock or build sees, or the manifest could require
   `cmake = { package, targets }`.

## Phases

1. **`[when]` for `defines` and `flags`:** the condition parser and evaluator,
   `Manifest.resolve`, validation, `host_settings` with its cache, export with `if()` and
   `$<CONFIG>`, and the README. Tests for parsing and validation, resolving with fake settings,
   the exported CMake for each row of the table, escaping, and `[when]` surviving `--update`.
2. **Link options, system libraries and conditional dependencies:** `link-flags`,
   `system-libs`, conditional dependencies, lock inputs, `cxy add --when windows`,
   `cxy lock --profile`, and CMake names. An end-to-end test on Linux can resolve the exported
   conanfile with a Windows MSVC profile.
3. **`shared` and conditional sources.**
4. **Import:** generator expressions through the hook, moved values, and the recorded
   configuration.

The MSVC branches are unit-tested on Linux by their text; the Windows CI job builds an exported
and a managed project with `[when.msvc]` for real.

## Decisions

The questions raised in review were answered as follows.

1. **`when` or `target`?** `when`. In CMake and in cxy, a target is a library or program, so
   `[target.core]` would read as a component. There is no `cfg()` wrapper.
2. **An `msvc-like` condition** for MSVC and clang-cl? Not for now. `msvc` is Conan's
   `compiler=msvc`, which clang-cl isn't, since Conan calls it `clang`. It can be added later, as
   a shorthand mapped to `CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC"`.
3. **Version comparisons,** such as `compiler.version < 12`? Not for now. Conditions test only
   equality; an operator can be added later without changing the syntax.
4. **May a `[when]` dependency without a version add options** to an unconditional one? No. A
   dependency in a `[when]` table is wholly conditional, with its own version, and is an error
   if it is also unconditional. This can be relaxed later, with options set in the conanfile's
   `configure()`.
5. **CMake names of conditional dependencies?** Both: `cxy.lock` records the names that each
   lock or build sees, and export uses them for a dependency that isn't in its graph. Without a
   recorded name, export stops with an error asking for `cmake = { package, targets }` in the
   manifest, rather than guessing `name::name`.
6. **`--update` on another configuration than the last import's?** An error, since a wrong
   update edits `cxy.toml`, unless an option says to go on.
