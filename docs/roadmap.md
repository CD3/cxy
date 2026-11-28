# Roadmap

Features to implement, in no particular order. Designs for review go in their own files in
`docs/`. Decisions go in `design-notes.md`.

## Keeping an imported cxy.toml up to date

`cxy import --update`, conflicts reported and decided by the user, and a warning when the
project's CMake or Conan files have changed. Designed in `import-update.md`.

## Editor tooling

Code completers and linters in Vim, Neovim and Helix should work in a cxy project without any
setup. Most of them use clangd, which needs a compile database.

- Today `compile_commands.json` is written to the build directory, under `build/cxy`, where
  clangd doesn't look.
- Options: link or copy it to the project root, or write a `.clangd` file that sets
  `CompilationDatabase` to the build directory.
- The database should exist before the first build, for example after `cxy build` only
  configures, or after a dedicated command such as `cxy editor` or `cxy setup`.
- Headers aren't in the database. clangd infers their flags, which usually works.
- Check what Helix's and Neovim's default language-server settings expect. Vim users may run
  clangd through ALE or coc.nvim.

## Install command

`cxy install`: build in Release and install the programs, and perhaps the libraries and headers,
to a prefix.

- Questions: the default prefix (`~/.local`, like `cargo install` uses `~/.cargo`), whether to
  install from a path or a Git URL as well as the current project, and whether libraries need
  CMake package files so that other projects can find them.

## Package command

`cxy package`: write a Conan recipe that packages the project, so other projects can require it,
and optionally run `conan create`.

- It builds on `cxy export`'s standalone `conanfile.py`, adding `package()`, `package_info()`,
  `exports_sources` and the components that `[lib.NAME]` tables describe.
- Libraries then need installed headers and CMake config files, which the install command needs
  as well.

## A build script from export

An option to `cxy export`, such as `--script`, that writes a short shell script running Conan and
CMake on the exported `conanfile.py` and `CMakeLists.txt`. It shows how to call the tools
correctly, and lets people who don't use cxy build the project.

- It should mirror what cxy runs: the profile, build type, generators, and the `--build=missing`
  and lockfile options, then the configure, build and test steps.
- Windows needs a PowerShell or batch version, or the script should be documented as POSIX only.

## Adding cxy's tool configuration to an existing project

`cxy new` and `cxy init` write `.clang-format` and `.clang-tidy`, but `cxy import` writes only
`cxy.toml`. An option, such as `cxy import --configs`, or a separate command, should add the
configuration files that cxy's tools use, such as `.clang-format`, `.clang-tidy` and
`.gersemirc`.

- Never overwrite a file the project already has: report it, and offer to show the difference.
- A team project may not want cxy's style imposed, so this stays opt-in.

## Custom default configuration files

Use the user's own defaults for the configuration files that `new`, `init` and the option above
write, instead of cxy's built-in ones, when they exist. For example,
`~/.config/cxy/defaults/.clang-tidy`.

- Follow the XDG base directories, `$XDG_CONFIG_HOME/cxy` with `~/.config/cxy` as the fallback,
  and the platform's equivalent on Windows and macOS.
- A file that exists replaces the built-in one. There is no merging.
- Perhaps a command to see which defaults are used, and where they come from.

## A skill for AI agents

A command, such as `cxy skill`, that writes instructions telling an AI coding agent how to use
cxy in the project: which commands to run for building, testing, formatting and linting, what
`cxy.toml` holds, and what not to edit by hand, such as `cxy.lock` and the files in `build/cxy`.

- Write it in the formats agents read, such as a `SKILL.md` for Claude Code (in
  `.claude/skills/cxy/`), or a section for `AGENTS.md` or `CLAUDE.md`.
- Generate it from the installed cxy's commands and help text, so it matches the version, and
  include project details such as the components and the test framework.
- Never overwrite a file the project already has. Report it, as for configuration files.

## Installing compilers

Trying the newest C++ features often needs a GCC or Clang newer than the system's package
manager has. cxy should install one, and build with it, without anyone building a compiler by
hand. It should use an existing compiler manager rather than build compilers itself.

- Candidates to evaluate:
  - conda-forge's prebuilt GCC and Clang, through pixi or micromamba: per-user, with no root
    needed, on Linux, macOS and Windows;
  - Homebrew's `gcc` and `llvm`, on macOS and Linux;
  - apt.llvm.org and the Ubuntu toolchain PPA, for Debian and Ubuntu, though they need root;
  - Spack, which builds compilers from source, including versions that no one packages yet;
  - xPack's GCC and Clang binaries;
  - Conan's own tool requirements, if ConanCenter has usable compiler recipes. This needs
    checking.
- Check for each one: how new its versions are, the platforms it covers, whether it needs root,
  how long an install takes, and whether its standard library and linker work with Conan
  packages built by the system compiler.
- cxy would then write a Conan profile for the installed compiler: `compiler`,
  `compiler.version`, `compiler.libcxx`, and the executables in
  `tools.build:compiler_executables`. You would choose it with `--profile`, or a setting in
  `cxy.toml`.
- Possible commands: `cxy toolchain add gcc@15`, `cxy toolchain list`, and
  `cxy toolchain remove`.
- Packages from ConanCenter must be built from source for a compiler version that has no
  prebuilt binaries. `--build=missing` already does that, but the first build is slow.
