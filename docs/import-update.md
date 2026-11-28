# Design: keeping an imported cxy.toml up to date

Status: proposed, for review.

## Problem

`cxy import` lets one person use cxy in a CMake and Conan project while the rest of the team
keeps their own setup. The team goes on changing the project: adding targets, requirements and
definitions, or moving directories. Today the person using cxy cannot bring those changes into
their `cxy.toml`:

- `cxy import` refuses while `cxy.toml` exists, even with `--force`. Deleting the file and
  importing again loses what they added themselves, such as a `[test]` setting or a dependency
  option.
- `cxy import --dry-run` prints a new proposal, but it has to be merged by hand.
- Nothing says that the project has changed. cxy goes on building from the old `cxy.toml`, and a
  missing definition or requirement shows up as a build failure, or not at all.

Files added to directories that cxy already knows need no import, since cxy finds sources by
directory. This design covers everything else.

## Proposal

1. `cxy import --update` imports again and merges the result into the existing `cxy.toml`.
2. When a setting has changed both in `cxy.toml` and in the project, cxy reports the conflict and
   asks which value to use.
3. `cxy build`, `run` and `test` warn when the project's CMake or Conan files have changed since
   the last import.

### What the last import wrote

A merge needs to know what changed on each side, so `cxy import` records what it proposed:

- the text of the `cxy.toml` it proposed, before any edits;
- a hash of each input: the conanfile, and the project's CMake files that the File API's
  `cmakeFiles` reply lists, which are the ones the configuration read.

It is kept in `cxy-import.json`, beside `cxy.toml`. It is not kept in `build/cxy`, which
`cxy clean` deletes, nor in `cxy.lock`, which is about dependencies and is remade by `cxy lock`.

### Merging

The merge compares three versions of each setting:

- **base:** what the last import proposed;
- **yours:** the current `cxy.toml`;
- **upstream:** what importing proposes now.

A setting is a value at a key path, such as `lib.text.defines` or `dependencies.fmt`. Arrays
and inline tables, such as `uses = [...]` or `fmt = { version = ..., options = ... }`, count as
one value, since their order and parts belong together.

| base vs yours | base vs upstream | Result                                  |
|---------------|------------------|-----------------------------------------|
| same          | same             | unchanged                               |
| same          | changed          | upstream's value, which may be a removal |
| changed       | same             | your value                              |
| changed       | changed          | the same both ways: that value; otherwise a conflict |

So nothing needs a list of which settings import owns:

- a setting you added stays, as long as upstream doesn't add the same one with another value;
- a setting upstream removed goes, unless you changed it, which is a conflict;
- a target renamed upstream is a removal plus an addition, so it conflicts only if you edited
  the old one.

Changes are applied to your file with tomlkit, so comments and formatting are kept.

After the merge, the result is parsed and its sources are found, as for a new import. An invalid
combination is an error, and nothing is written. An example is a `uses` of yours that names a
library upstream removed. The problems that import finds work as they do now: `cxy.toml` is
updated only with `--force`.

### Conflicts

Each conflict is shown with all three values, and cxy asks which one to use:

```
Conflict in lib.text.defines
  last import:  ["TEXT_API=1"]
  yours:        ["TEXT_API=1", "LOUD"]
  upstream:     ["TEXT_API=2"]
Use yours or upstream's? [y/u]
```

- A removal shows as `(removed)`.
- No other value is offered. To combine both, keep yours, then edit the file. The record makes
  upstream's value the new base, so the next update won't ask again.
- When nothing can be asked, because there is no terminal (as in CI), cxy lists the conflicts,
  writes nothing, and exits with an error. `--resolve yours` or `--resolve upstream` decides them
  all without asking.
- `--update --dry-run` lists the changes and conflicts without asking or writing.

After a successful update, cxy prints each change it made, for example
`lib.core.defines: ["A"] → ["A", "B"]`, and writes a new record.

### Noticing changes

`cxy build`, `run` and `test` hash the recorded inputs, and warn when one has changed:

```
Warning: CMakeLists.txt has changed since cxy.toml was imported; run cxy import --update
```

- Hashing a few files costs little, and nothing is configured.
- It is a warning, not an error, since many CMake changes don't affect cxy's build.
- A new CMake file that an existing one includes changes the file that includes it, so it is
  noticed too.

## Without a record

A `cxy.toml` imported before this change, or one the record was deleted for, has no base. Then
every difference between yours and upstream's is a conflict, which is the safe choice. The
update writes a record, so later updates merge normally.

## Not covered

- **Changes made in cxy, sent to the team.** That direction is `cxy export`, which writes the
  project's own files for people who don't use cxy. It doesn't apply to a project the team
  maintains by hand.
- **Restructuring a project into cxy's layout.** That is a separate command, possibly `migrate`.

## Questions for review

1. **Should the record be committed?** If `cxy.toml` is committed, the record should be too, so
   that a second cxy user doesn't see every difference as a conflict. If `cxy.toml` stays out of
   the team's repository, the record does too. The README should say so either way.
2. **Name and format of the record.** The proposal is `cxy-import.json`. One alternative is a
   `.cxy/` directory, if more per-project state is expected.
3. **Should a changed input make `build` an error?** That would need an option to go on anyway.
   The proposal is a warning.
4. **Should `--update` be the default when `cxy.toml` exists?** The proposal keeps it explicit,
   so that a plain `cxy import` never changes an existing file.
