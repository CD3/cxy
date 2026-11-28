# Changelog

## Unreleased

### Added

- `[when.'<condition>']` tables in `cxy.toml` add definitions and flags for some platforms,
  compilers or build types, such as `[when.windows.build]` or `[when.msvc.lib.core]`. Managed
  builds test them against the Conan profile, and `cxy export` writes them as CMake conditions.
- `cxy import --update` merges the project's changes since the last import into `cxy.toml`,
  keeping your own, and asks which value to use when a setting changed on both sides.
  `--resolve yours` or `--resolve upstream` decides without asking. `cxy import` now also writes
  `cxy-import.json`, the record that the merge needs.
- `cxy build`, `run` and `test` warn when the CMake or Conan files that `cxy.toml` was imported
  from have changed.

### Changed

- Managed builds write a `CMakeLists.txt` for each build type, in `build/cxy/source/`, so
  Debug and Release builds no longer reconfigure each other.
- `cxy port` is now `cxy import`, the reverse of `cxy export`. It writes a `cxy.toml` that
  describes the project as it is, and never changes the project's layout, which "port"
  suggested. There is no `port` alias: run `cxy import` instead, with the same options. Its
  work directory is now `build/cxy/import`, so `build/cxy/port` can be deleted.
