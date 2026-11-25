# Design notes

## To reconsider

- **Which tools to run.** Tools on `PATH` currently win over the copies installed with cxy, so
  that cxy does not migrate a shared Conan cache between versions. Tools from a project-level
  cache may turn out to be useful at times, for example to pin versions per project. Revisit
  when there is a concrete need. See `core/tools.py`.
- **Caching lint results.** clang-tidy runs on every file each time. A cache must account for every
  included header, including those outside the layout, or it would hide problems.

## Decided

- **Test programs.** Tests beside sources are separate programs so that code can be ported
  incrementally after a breaking change. Sources in `tests/` share one program.
- **Isolated tests.** Separate programs are not enough for that: each links the library, which
  needs every library source to compile. `cxy test --isolated` therefore compiles only what a
  test needs. It relies on naming conventions, so it is not the default and is not exported.
- **Debug is the default build type.**
- **`clean` only deletes a directory that cxy created.** cxy keeps its files in `build/cxy`.
- **Formatters run directly, not through CMake targets.** That needs no configure step, works in
  projects that do not use cxy, and keeps exports clean.
- **cxy writes formatted files itself.** Tools only produce text, so check, diff and write modes
  are the same for all of them. Tools are given paths where possible: clang-format 18 does not
  apply `.clang-format-ignore` to stdin, and older gersemi cannot find its configuration for it.
- **A check that could not run is a failure.** Otherwise a missing tool or compile database would
  make CI pass. The exception is cmake-lint, which is no longer maintained and so is optional.
- **cxy never configures a project that it does not manage**, because that runs its code.
- **`cxy.lock` wraps Conan's lockfile with the settings it was made from.** Conan's lockfile records
  overrides that win over the recipe's, even in strict mode, so on its own it would silently ignore
  changes to `[overrides]`. cxy compares the settings, and discards recorded overrides when resolving.
- **A lock is pruned only when dependencies change**, since Conan cannot tell unneeded entries from
  those for other platforms.
- **Unknown options are errors.** Conan ignores them when they come from `default_options`. The graph
  omits options that a recipe removes for the current configuration, such as fmt's `shared` when
  `header_only` is set, so `conan inspect` confirms before anything is rejected.
- **Test frameworks are detected, one per program.** The manifest shows which are available; if there
  is more than one, the includes decide. Two frameworks in one program would give the linker two
  main() functions, and it may silently choose one, so that the other's tests never run.
- **cxy checks for itself that some gtest tests ran**, since most versions succeed when a filter
  matches nothing.
