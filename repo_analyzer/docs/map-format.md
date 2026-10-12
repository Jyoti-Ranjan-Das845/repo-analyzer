# Component map format

The component map says which code belongs to which component and subcomponent. It is the only input the
scorer needs besides the code itself. Everything else (lines, calls, writes, complexity) is counted by code.
All paths are relative to the project folder.

## File

One JSON file:

```json
{
  "repo": "project-name",
  "commit": "the checked-out commit, or empty outside git",
  "tests": ["tests/", "src/pkg/testing_helpers.py"],
  "excluded": [
    { "path": "docs/", "reason": "documentation" },
    { "path": "vendor/", "reason": "copied third-party code" }
  ],
  "components": [
    {
      "id": "c1",
      "name": "Query engine",
      "kind": "feature",
      "description": "What this component is responsible for, in one or two sentences.",
      "subcomponents": [
        {
          "id": "c1s1",
          "name": "SQL builder",
          "description": "What this part does, in one or two sentences.",
          "owns": ["src/query/", "src/utils.py::build_where", "src/app.py::App.run_query"]
        }
      ]
    }
  ]
}
```

## Fields

| Field | Meaning |
|---|---|
| `repo`, `commit` | The project's name and commit. Filled in by the tool; leave them as they are |
| `tests` | Test code: entries in any of the forms below. Tests belong to no component |
| `excluded` | Code that is not part of the product: documentation, copied third-party code, generated files, project config. Each with a short reason |
| `components[].id` | `c1`, `c2`, ... |
| `components[].kind` | `feature`, `foundation` or `supporting` (see below) |
| `subcomponents[].id` | Component id + `s` + number: `c1s1`, `c1s2`, ... |
| `subcomponents[].owns` | The code this subcomponent owns, as a list of entries |

Every component has at least one subcomponent. A component that does not split has exactly one.

## Component kinds

| Kind | Meaning |
|---|---|
| `feature` | Something the software does for its users, such as search, export or login |
| `foundation` | Code with no purpose of its own that many other components rely on: helpers, storage access, configuration, logging |
| `supporting` | Code that serves the project rather than its users: build scripts, developer tools, migrations, benchmarks |

## Entry formats

| Entry | Means |
|---|---|
| `src/search/` | Everything in this folder |
| `src/search/query.py` | This whole file |
| `src/utils.py::build_where` | Only this function or class (a class includes its methods) |
| `src/app.py::App.run_query` | Only this method |
| `infra/main.tf#L10-L80` | Only these lines. Last resort, for files with no named pieces |

Piece names must be copied exactly from the piece list the reader produces (`pieces.md`).

## Rules

1. **Ownership follows responsibility.** Code belongs to the subcomponent whose job it is, even when many others
   use it. Calling code creates a link, not ownership.
2. **Foundation is only for code with no single job**: helpers, storage access, configuration, logging. A model or
   class that represents one domain concept belongs to that domain's component.
3. **Use the biggest unit that is true.** A folder when the whole folder belongs, a file when the whole file
   belongs, a function or method only when a file mixes code of different subcomponents.
4. **The most specific entry wins.** A line range beats a method, a method beats its class, a class or function
   beats its file, a file beats its folder, a deeper folder beats a shallower one. So a folder can go to one
   subcomponent while a single function inside it goes to another.
5. **Every text file is covered exactly once**: by a subcomponent, `tests` or `excluded`. The files that count are
   the ones listed in `pieces.md` (binary and very large files are not analysed and need no entry). Two entries of
   the same specificity may not give the same code to two different owners.
6. **One function doing two jobs is not split.** Give it to the owner of its main job.
7. **Code outside any named piece** (imports, constants, top-level statements) in a split file goes automatically
   to the owner with the most lines in that file. A class header goes to the owner with the most lines in the class.

## Checking a map

```bash
repo-analyzer check <project folder>          # or: python -m repo_analyzer check <project folder>
```

It reports every uncovered file, unknown path or piece name, conflict and empty subcomponent, or prints `OK` with
the number of lines each owner has.
