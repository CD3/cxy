# Changelog

## Unreleased

### Added

- `[when.'<condition>']` tables in `cxy.toml` add definitions and flags for some platforms,
  compilers or build types, such as `[when.windows.build]` or `[when.msvc.lib.core]`. Managed
  builds test them against the Conan profile, and `cxy export` writes them as CMake conditions.
- `link-flags` and `system-libs` settings, in `[build]`, `[lib]`, `[bin]` and their named and
  `[when]` forms. `link-flags` is only for `[build]` and programs. `cxy import` now carries a
  project's own link options into them, rather than reporting them as problems.
- `[when.'<condition>'.dependencies]` and `dev-dependencies`, for packages that only some
  platforms need, and `cxy add --when <condition>`. The exported conanfile and CMakeLists.txt
  ask for them only on those platforms.
- `cxy lock --profile A --profile B` locks for several platforms at once. `cxy.lock` records the
  CMake names of conditional dependencies, which `cxy export` needs where they aren't installed.
- `cxy import --update` merges the project's changes since the last import into `cxy.toml`,
  keeping your own, and asks which value to use when a setting changed on both sides.
  `--resolve yours` or `--resolve upstream` decides without asking. `cxy import` now also writes
  `cxy-import.json`, the record that the merge needs.
- `cxy build`, `run` and `test` warn when the CMake or Conan files that `cxy.toml` was imported
  from have changed.

### Changed

- `cxy lock` takes `--profile`, `--release` and `--verbose` itself, and `--profile` may be
  repeated.
- Managed builds write a `CMakeLists.txt` for each build type, in `build/cxy/source/`, so
  Debug and Release builds no longer reconfigure each other.
- `cxy port` is now `cxy import`, the reverse of `cxy export`. It writes a `cxy.toml` that
  describes the project as it is, and never changes the project's layout, which "port"
  suggested. There is no `port` alias: run `cxy import` instead, with the same options. Its
  work directory is now `build/cxy/import`, so `build/cxy/port` can be deleted.
