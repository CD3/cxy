# Design notes

## To reconsider

- **Which tools to run.** Tools on `PATH` currently win over the copies installed with cxy, so
  that cxy does not migrate a shared Conan cache between versions. Tools from a project-level
  cache may turn out to be useful at times, for example to pin versions per project. Revisit
  when there is a concrete need. See `core/tools.py`.

## Decided

- **Test programs.** Tests beside sources are separate programs so that code can be ported
  incrementally after a breaking change. Sources in `tests/` share one program.
- **Isolated tests.** Separate programs are not enough for that: each links the library, which
  needs every library source to compile. `cxy test --isolated` therefore compiles only what a
  test needs. It relies on naming conventions, so it is not the default and is not exported.
- **Debug is the default build type.**
- **`clean` only deletes a directory that cxy created.** cxy keeps its files in `build/cxy`.
