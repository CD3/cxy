# Changelog

## Unreleased

### Changed

- `cxy port` is now `cxy import`, the reverse of `cxy export`. It writes a `cxy.toml` that
  describes the project as it is, and never changes the project's layout, which "port"
  suggested. There is no `port` alias: run `cxy import` instead, with the same options. Its
  work directory is now `build/cxy/import`, so `build/cxy/port` can be deleted.
