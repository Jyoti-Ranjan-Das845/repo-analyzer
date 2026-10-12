# Effort scoring (components, subcomponents, repo)

Status: draft v1.3, 2026-10-10 (rules tightened after the datasette check, see evaluation-datasette-scores.md), implemented in `repo_analyzer/` (reader.py, contract.py, metrics.py). Every rule is deterministic and works for any language. The weights and benchmark
averages are starting guesses. They will be learned from real agent runs (tokens, tool calls, failures).

## Inputs

* **Parser (code, no AI):**
  * every function and class, with its file and non-blank lines
  * who calls whom
  * writes to shared things
  * hidden-flow sites
  * the 5 complexity counts
* **Agent map (AI):**
  * the owner component and subcomponent of every function and class
  * which files are tests

## What an AI agent spends, and the metrics that cause it

| Spend | Metrics |
|---|---|
| Read (tokens in) | L, X, R, N |
| Trips (tool calls and rounds) | F, H, K, E, D |
| Think (reasoning) | C |
| Redo (wasted work) | T |

## Metrics

| Metric | Rule |
|---|---|
| **L** own lines | Non-blank lines of every function and class the owner holds |
| **X** outside lines | Functions in other owners that its code calls directly (one step). Their lines, each counted once. Third-party code is excluded. |
| **R** caller lines | Functions in other owners that call its code directly. Their lines, each counted once. Tests are excluded (see T). |
| **N** noise lines | Other code lying between the part's own blocks in a file (blank lines excluded). Each gap counts at most 50 lines, because a longer gap is skipped with one search, which costs one step. Code before the first and after the last own block is not counted |
| **F** files | Distinct files holding own, outside and caller code |
| **H** hops | Rounds of opening newly discovered files: <br>(1) Start with the files of its entry points: functions called from other owners, or called by nothing (tests do not count as callers). Own files nothing leads to are also opened at the start. <br>(2) Reading an opened file reveals the own files its import lines point to and the files of everything its own code calls. <br>(3) All newly revealed files are opened together, as one round. Outside code is opened but not followed further. <br>H = the number of rounds after the start. |
| **K** name clashes | For each name the part calls as a plain call (`foo()`, no object in front), the number of other top-level functions and classes in the repo with that name (same language). Calls through an object, a module or a class are found through that object, and a name an import line brings in (`from .utils import helper`) through that line, so they are not counted. Local names (variables, nested functions) are not counted |
| **E** outward effects | Distinct targets its code assigns to that are not local variables: fields (`a.b = …`), elements (`a[i] = …`), module or global variables, contents of parameters. A field written through `self`/`this` is named after its class (`self.x` in class A is `A.x`). Method calls that change an object (e.g. `append`) are not counted, because syntax cannot tell which methods change things. |
| **D** hidden flow | One spot per decorated definition (stacked decorators count once); one per function that calls a function it received as a parameter; one per distinct call whose target is picked at run time (`handlers[k]()`); one per function or lambda handed to other code (as an argument or stored in a collection), including a repo function passed by name |
| **T** test protection | Start from every call in test code, follow calls through any code; every function reached is tested (a class body when the class is built or a method runs; top-level code when its file is used, or loaded by the tests' imports directly or through other imports). T = own lines in tested code ÷ L |

**Linking a call to its function** (used by X, R, H, T):

1. **Through a name an import line brought in**, in any language: `bodyParser.json()` after `var bodyParser = require('..')`, `encoding.want_bytes()` after `from itsdangerous import encoding`, `helper()` after `from .utils import helper`, `Signer.sign()` after `from .signer import Signer`. The call is looked up in the project file the import points to, then in the files that file imports, up to two steps on (for files that pass names on, like `index.js` or `__init__.py`), nearest first. One match: linked, however short the name. Several: unlinked. A renamed import (`a as b`) is looked up by its original name. An import that points outside the project (a standard or installed library: `os.close()`, `sqlite3.connect()`) is never linked to project code, even when the project has a function with that name. A local variable or parameter with the same name hides the import.
2. **Otherwise**, only code in the caller's language. A plain call reaches a top-level function, a class or a method of the caller's own class, and never a local name. Through `self`/`this` it reaches the caller's class; through a class name, that class. A call on an object of unknown type is linked only when the method name is distinctive (compound like `render_template`, or 10+ characters), because short single words (`get`, `append`, `all`) are shared with standard libraries. Among several candidates, the fixed order is: the only one; the only one in the same file; the only one in a file the caller imports. Otherwise the call stays unlinked.

**Matching import lines to files** is done by paths, the same way for every language:

* A quoted path (`'./lib/read'`, `"util/strings.h"`, `"github.com/x/app/internal/util"`) is a file path; one starting with `./` or `../` (or `.`, `..`) is relative to the importing file. An unquoted name (`itsdangerous.signer`, `.signer`, `crate::parser`, `App\Models\User`) is a module path whose parts are separated by `.`, `::` or `\`; leading dots and `self::`/`super::` make it relative. In `from a import b`, `a/b` is tried too, because `b` may be a module.
* A path names the project file or folder it leads to from the importing file, or else the one whose path ends with the same parts (`src/itsdangerous/signer.py` for `itsdangerous.signer`; the folder `internal/util` for the Go path above). A one-part path matches only a folder with a main file or a file at the top of the project: `import json` is the standard library, not `lib/types/json.js`.
* A folder stands for its main file (`index.*`, `__init__.*`, `mod.*`, `main.*`, `lib.*`), else for all its files in the importing file's language (a Go or Java package). A module path like `a.b.c` also loads the main file of each folder it passes through, as Python runs a package's `__init__.py` first.
* An import that matches no project file, and none of whose parts names a project folder (or the project itself), points outside the project.

## Complexity C

Counted per function, over own + outside code:

* **Decisions:** +1 for each if / else-if / case / ternary / loop / catch / and-or / early return-break-continue-throw / recursion.
* **Nesting:** the depth of each decision.
* **Data dependencies:** for each read of a variable, the assignments to it that could have produced the value: earlier in the function, or anywhere inside a loop that contains the read. A parameter counts as one assignment at the start.
* **Vocabulary:** distinct names in the function.
* **Concurrency:** async, await, thread or task start, lock, channel.

Steps:

1. Add up each count.
2. Divide by every 100 lines.
3. Divide by the typical value to get a ratio. Counts and typical values are taken over code lines only; lines with no program logic (templates, styles, data, docs) count as typical, 1.0. Until a benchmark set of repositories exists, the typical value is the repository's own average, so 1.0 means "average for this codebase".
4. **C = average of the 4 everyday ratios + concurrency ratio ÷ 5.**

C = 1 means average code with no concurrency. Concurrency only adds when it is present.

## Formula

```
Effort = (L + X) × C  +  N  +  w × (F + H + K + E + D)  +  (L + R) × (1 − T)        w = 50 (guess)
```

The unit is line-equivalents. Multiply by about 10 for tokens.

## Levels and scaling

Report two numbers at every level:

* **Effort** (absolute): how big the job is.
* **Effort per 100 own lines** (normalized): how hard per line. It is comparable across sizes and across repos.

Rules by level:

* **Subcomponent:** the same rules with the subcomponent as owner. Sibling code counts as outside code, caller code or noise, so subcomponents do not add up to their component.
* **Repo:** the whole repo as one owner. X = R = N = 0, and each trip is counted once. The repo is not the sum of components, because shared code would be counted many times.
* **Never score with** component counts, subcomponent counts or per-component averages. They measure how the map was cut, not the code.

## Human weights

The metrics stay the same; only the weights change:

* N ≈ 0, because people skim.
* K ≈ 0, because the editor's go-to-definition finds the right one.
* w is smaller.
* C is weighted more.

## Worked example: ShopApp (5 files, 1,050 lines)

| File | Lines | Functions (lines) |
|---|---|---|
| cart.py | 220 | add_item 60, remove_item 40, cart_total 70, apply_coupon 50 |
| payment.py | 180 | charge 90, refund 90 |
| utils.py | 400 | refund_rules 60, retry 40, log 30, format_money 50, parse_date 120, slugify 100 |
| db.py | 150 | save 80, load 70 |
| cache.py | 100 | save 50, load 50 |

Tests call add_item, cart_total and charge.

The map:

* **Cart:** 1a Items (add_item, remove_item) and 1b Pricing (cart_total, apply_coupon).
* **Payments:** 2a Charge (charge) and 2b Refund (refund, plus refund_rules, which lives in utils.py).
* **Foundation:** 3a Storage (both save/load pairs) and 3b Helpers (the other utils functions).

Results. The benchmark averages used are 10 / 12 / 30 / 40 / 2 per 100 lines.

| Owner | L | X | R | N | F | H | K | E | D | T | C | Effort | Per 100 lines |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Cart | 220 | 160 | 90 | 0 | 4 | 1 | 1 | 2 | 0 | 0.59 | 0.90 | 867 | 394 |
| Payments | 240 | 320 | 0 | 340 | 5 | 2 | 2 | 2 | 2 | 0.38 | 1.18 | 1,802 | 751 |
| Foundation | 590 | 0 | 410 | 60 | 5 | 0 | 0 | 3 | 1 | 0.34 | 0.80 | 1,643 | 278 |
| Repo | 1,050 | 0 | 0 | 0 | 5 | 0 | 2 | 7 | 3 | 0.40 | 1.05 | 2,580 | 246 |

Subcomponents (effort / per 100 lines):

| Subcomponent | Effort | Per 100 lines |
|---|---|---|
| 1a Items | 624 | 624 |
| 1b Pricing | 614 | 512 |
| 2a Charge | 891 | 990 |
| 2b Refund | 1,514 | 1,010 |
| 3a Storage | 928 | 371 |
| 3b Helpers | 1,064 | 313 |

What-if: moving refund_rules into payment.py brings Payments from 1,802 to 1,412 (−22%). N drops to 0 and H to 1.

## Open questions

* What w should be, and whether a hop should cost more than a file. Settle from agent runs.
* Whether cost grows faster than size for an AI (long context).
* Whether an AI pays Map and Depth again every session (it has no memory).
* N assumes the agent reads whole home files.
* Whether unused code should count.
